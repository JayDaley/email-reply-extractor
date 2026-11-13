//! Fragment scanner: segment an email body into quoted / unquoted fragments.
//!
//! Port of the reference's `_fragments.py`, itself trimmed from
//! `email_reply_parser` 0.5.12 (MIT License, Copyright (c) 2012 Zapier LLC).
//! The scan works bottom-up: lines are visited last-to-first, and a fragment
//! grows while successive lines share its quoted/header classification. A
//! blank line directly above a signature-looking line closes the fragment.
//!
//! Before the scan, a wrapped "On … wrote:" attribution is collapsed onto
//! one line by a substitution whose replacement is the first match's text
//! (newlines removed) used as a Python `re.sub` *template*. Python processes
//! backslash escapes in it, and an invalid escape raises `re.error`; the port
//! reproduces the template rules ([`parse_template`]) and reports such a
//! failure as [`PatternError`], which `extract_new_text` turns into a
//! `failed` result.
//!
//! The upstream patterns are matched by hand here; each function names the
//! pattern it implements.

use crate::pystr;

/// One scanned fragment: its content (edges kept) and whether it is quoted.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fragment {
    /// The fragment's lines joined by `\n`, in document order.
    pub content: String,
    /// Whether the scanner classified the fragment as quoted.
    pub quoted: bool,
}

/// The replacement template of the attribution collapse was invalid (Python
/// raises `re.error`, recorded as `PatternError`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PatternError;

impl std::fmt::Display for PatternError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("invalid replacement template in the attribution collapse")
    }
}

impl std::error::Error for PatternError {}

struct OpenFragment {
    quoted: bool,
    headers: bool,
    lines: Vec<String>,
}

impl OpenFragment {
    fn close(self) -> Fragment {
        let mut lines = self.lines;
        lines.reverse();
        Fragment {
            content: lines.join("\n"),
            quoted: self.quoted,
        }
    }
}

/// Scan `text` (LF-normalized) into document-order fragments.
pub fn read_fragments(text: &str) -> Result<Vec<Fragment>, PatternError> {
    let text = collapse_wrapped_attribution(text)?;
    let text = break_outlook_boundaries(&text);

    let mut fragments: Vec<Fragment> = Vec::new();
    let mut open: Option<OpenFragment> = None;
    for line in text.split('\n').rev() {
        open = scan_line(&mut fragments, open, line);
    }
    if let Some(f) = open {
        fragments.push(f.close());
    }
    fragments.reverse();
    Ok(fragments)
}

// --- the wrapped-attribution collapse -----------------------------------------

/// `_MULTI_QUOTE_HDR_SEARCH_RE.search` and `_MULTI_QUOTE_HDR_SUB_RE.sub`:
/// the pattern `(?!On.*On\s.+?wrote:)(On\s(.+?)wrote:)` under `re.DOTALL`.
///
/// The negative lookahead rejects a start position from which a later
/// `On\s` is followed (after at least one character) by `wrote:`. So the
/// only position that can match is the last `On\s` that some `wrote:`
/// follows, and the pattern matches at most once in a string: the search
/// and the substitution see the same single match.
fn collapse_wrapped_attribution(text: &str) -> Result<String, PatternError> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let wrote: Vec<char> = "wrote:".chars().collect();
    let wrote_at = |i: usize| i + 6 <= n && chars[i..i + 6] == wrote[..];
    let Some(last_wrote) = (0..n).rev().find(|&i| wrote_at(i)) else {
        return Ok(text.to_string());
    };
    if last_wrote < 4 {
        return Ok(text.to_string());
    }
    // The last "On\s" at or before last_wrote - 4.
    let Some(p) = (0..=last_wrote - 4).rev().find(|&p| {
        p + 2 < n && chars[p] == 'O' && chars[p + 1] == 'n' && pystr::is_space(chars[p + 2])
    }) else {
        return Ok(text.to_string());
    };
    let r = (p + 4..n)
        .find(|&r| wrote_at(r))
        .expect("last_wrote >= p + 4");
    let whole: String = chars[p..r + 6].iter().collect();
    let inner: String = chars[p + 3..r].iter().collect();
    let template: Vec<char> = whole.chars().filter(|&c| c != '\n').collect();
    let pieces = parse_template(&template, 2)?;
    let mut out: String = chars[..p].iter().collect();
    for piece in pieces {
        match piece {
            Piece::Literal(s) => out.push_str(&s),
            Piece::Group(0) | Piece::Group(1) => out.push_str(&whole),
            Piece::Group(_) => out.push_str(&inner),
        }
    }
    out.extend(&chars[r + 6..]);
    Ok(out)
}

/// A parsed `re.sub` replacement template element.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Piece {
    Literal(String),
    Group(usize),
}

/// One token of Python's template tokenizer: a character, or a backslash and
/// the character after it.
fn tokens(t: &[char]) -> Result<Vec<Vec<char>>, PatternError> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < t.len() {
        if t[i] == '\\' {
            if i + 1 >= t.len() {
                return Err(PatternError); // bad escape (end of pattern)
            }
            out.push(vec!['\\', t[i + 1]]);
            i += 2;
        } else {
            out.push(vec![t[i]]);
            i += 1;
        }
    }
    Ok(out)
}

fn is_single(tok: Option<&Vec<char>>, set: &str) -> bool {
    matches!(tok, Some(t) if t.len() == 1 && set.contains(t[0]))
}

const OCT: &str = "01234567";
const DIGITS: &str = "0123456789";

/// Python's `re._parser.parse_template` for a pattern with `groups` groups
/// and no named groups (CPython 3.14).
pub(crate) fn parse_template(t: &[char], groups: usize) -> Result<Vec<Piece>, PatternError> {
    let toks = tokens(t)?;
    let mut k = 0;
    let mut result: Vec<Piece> = Vec::new();
    let mut literal = String::new();
    let add_group = |index: usize, literal: &mut String, result: &mut Vec<Piece>| {
        if index > groups {
            return Err(PatternError);
        }
        if !literal.is_empty() {
            result.push(Piece::Literal(std::mem::take(literal)));
        }
        result.push(Piece::Group(index));
        Ok(())
    };
    while k < toks.len() {
        let this = toks[k].clone();
        k += 1;
        if this[0] != '\\' || this.len() == 1 {
            literal.push(this[0]);
            continue;
        }
        let c = this[1];
        if c == 'g' {
            if toks.get(k).map(|t| t.as_slice()) != Some(&['<'][..]) {
                return Err(PatternError); // missing <
            }
            k += 1;
            let mut name: Vec<char> = Vec::new();
            loop {
                let Some(tok) = toks.get(k) else {
                    return Err(PatternError); // missing >
                };
                k += 1;
                if tok.as_slice() == ['>'] {
                    if name.is_empty() {
                        return Err(PatternError); // missing group name
                    }
                    break;
                }
                name.extend(tok);
            }
            if name.iter().all(|c| c.is_ascii_digit()) {
                let digits: String = name.iter().collect::<String>();
                let trimmed = digits.trim_start_matches('0');
                // An index above the group count is an error; the digits may
                // be arbitrarily long.
                if trimmed.len() > 3 {
                    return Err(PatternError);
                }
                let index: usize = if trimmed.is_empty() {
                    0
                } else {
                    trimmed.parse().map_err(|_| PatternError)?
                };
                add_group(index, &mut literal, &mut result)?;
            } else {
                // No named groups: a valid identifier raises IndexError, an
                // invalid one re.error. Both are errors.
                return Err(PatternError);
            }
        } else if c == '0' {
            let mut digits = String::new();
            if is_single(toks.get(k), OCT) {
                digits.push(toks[k][0]);
                k += 1;
                if is_single(toks.get(k), OCT) {
                    digits.push(toks[k][0]);
                    k += 1;
                }
            }
            let value = if digits.is_empty() {
                0
            } else {
                u32::from_str_radix(&digits, 8).map_err(|_| PatternError)?
            };
            literal.push(char::from_u32(value & 0xff).ok_or(PatternError)?);
        } else if DIGITS.contains(c) {
            let mut text: Vec<char> = vec![c];
            let mut is_octal = false;
            if is_single(toks.get(k), DIGITS) {
                text.push(toks[k][0]);
                k += 1;
                if OCT.contains(c) && OCT.contains(text[1]) && is_single(toks.get(k), OCT) {
                    text.push(toks[k][0]);
                    k += 1;
                    is_octal = true;
                    let s: String = text.iter().collect();
                    let value = u32::from_str_radix(&s, 8).map_err(|_| PatternError)?;
                    if value > 0o377 {
                        return Err(PatternError);
                    }
                    literal.push(char::from_u32(value).ok_or(PatternError)?);
                }
            }
            if !is_octal {
                let s: String = text.iter().collect();
                let index: usize = s.parse().map_err(|_| PatternError)?;
                add_group(index, &mut literal, &mut result)?;
            }
        } else {
            let escaped = match c {
                'a' => Some('\u{7}'),
                'b' => Some('\u{8}'),
                'f' => Some('\u{c}'),
                'n' => Some('\n'),
                'r' => Some('\r'),
                't' => Some('\t'),
                'v' => Some('\u{b}'),
                '\\' => Some('\\'),
                _ => None,
            };
            match escaped {
                Some(ch) => literal.push(ch),
                None if c.is_ascii_alphabetic() => return Err(PatternError), // bad escape
                None => {
                    literal.push('\\');
                    literal.push(c);
                }
            }
        }
    }
    if !literal.is_empty() {
        result.push(Piece::Literal(literal));
    }
    Ok(result)
}

/// `re.sub("([^\n])(?=\n ?[_-]{7,})", "\\1\n", text)`: break the reply off
/// an Outlook-style boundary line it sits directly on top of.
fn break_outlook_boundaries(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut out = String::with_capacity(text.len());
    for i in 0..n {
        out.push(chars[i]);
        if chars[i] == '\n' || i + 1 >= n || chars[i + 1] != '\n' {
            continue;
        }
        let mut j = i + 2;
        if j < n && chars[j] == ' ' {
            j += 1;
        }
        let run = chars[j..]
            .iter()
            .take(7)
            .filter(|&&c| c == '_' || c == '-')
            .count();
        if run == 7 && chars[j..j + 7].iter().all(|&c| c == '_' || c == '-') {
            out.push('\n');
        }
    }
    out
}

// --- the line scan ------------------------------------------------------------

/// `_QUOTE_HDR_RE.match`: `On.*wrote:$` on a line without `\n`.
fn is_quote_header(line: &str) -> bool {
    line.starts_with("On") && line.ends_with("wrote:") && line.len() >= 8
}

/// `_HEADER_RE.match`: `^\*?(From|Sent|To|Subject):\*? .+`.
fn is_header(line: &str) -> bool {
    let s = line.strip_prefix('*').unwrap_or(line);
    for word in ["From", "Sent", "To", "Subject"] {
        if let Some(rest) = s.strip_prefix(word) {
            if let Some(rest) = rest.strip_prefix(':') {
                let rest = rest.strip_prefix('*').unwrap_or(rest);
                if let Some(rest) = rest.strip_prefix(' ') {
                    return rest.chars().next().is_some_and(|c| c != '\n');
                }
            }
        }
    }
    false
}

/// `_SIG_RE.match`: `(--|__|-\w)|(^Sent from my (\w+\s*){1,3})`.
fn is_signature_start(s: &str) -> bool {
    if s.starts_with("--") || s.starts_with("__") {
        return true;
    }
    let mut it = s.chars();
    if it.next() == Some('-') && it.next().is_some_and(pystr::is_word) {
        return true;
    }
    s.strip_prefix("Sent from my ")
        .and_then(|rest| rest.chars().next())
        .is_some_and(pystr::is_word)
}

fn scan_line(
    fragments: &mut Vec<Fragment>,
    mut open: Option<OpenFragment>,
    line: &str,
) -> Option<OpenFragment> {
    let quote_header = is_quote_header(line);
    let quoted = line.starts_with('>');
    let header = quote_header || is_header(line);
    let blank = !pystr::has_content(line);

    if blank {
        if let Some(f) = &open {
            let last = f.lines.last().map(String::as_str).unwrap_or("");
            if is_signature_start(pystr::strip(last)) {
                fragments.push(open.take().expect("open").close());
            }
        }
    }

    if let Some(f) = &mut open {
        if (f.headers == header && f.quoted == quoted) || (f.quoted && (quote_header || blank)) {
            f.lines.push(line.to_string());
            return open;
        }
    }
    if let Some(f) = open {
        fragments.push(f.close());
    }
    Some(OpenFragment {
        quoted,
        headers: header,
        lines: vec![line.to_string()],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tpl(s: &str) -> Result<Vec<Piece>, PatternError> {
        parse_template(&s.chars().collect::<Vec<_>>(), 2)
    }

    #[test]
    fn template_rules() {
        assert_eq!(tpl("a$1").unwrap(), vec![Piece::Literal("a$1".into())]);
        assert_eq!(tpl(r"a\nb").unwrap(), vec![Piece::Literal("a\nb".into())]);
        assert_eq!(tpl(r"\.").unwrap(), vec![Piece::Literal("\\.".into())]);
        assert_eq!(
            tpl(r"x\1").unwrap(),
            vec![Piece::Literal("x".into()), Piece::Group(1)]
        );
        assert_eq!(tpl(r"\g<2>").unwrap(), vec![Piece::Group(2)]);
        assert_eq!(tpl(r"\101").unwrap(), vec![Piece::Literal("A".into())]);
        assert!(tpl(r"C:\Users").is_err());
        assert!(tpl(r"\3").is_err());
        assert!(tpl(r"\g<a>").is_err());
        assert!(tpl("x\\").is_err());
        assert!(tpl(r"\400").is_err());
    }

    #[test]
    fn outlook_boundary() {
        assert_eq!(break_outlook_boundaries("a\n_______"), "a\n\n_______");
        assert_eq!(break_outlook_boundaries("a\n -------x"), "a\n\n -------x");
        assert_eq!(break_outlook_boundaries("a\n------"), "a\n------");
        assert_eq!(break_outlook_boundaries("\n\n_______"), "\n\n_______");
    }
}
