//! Fragment scanner: segment an email body into quoted / unquoted fragments.
//!
//! Port of the reference's `_fragments.py`, itself trimmed from
//! `email_reply_parser` 0.5.12 (MIT License, Copyright (c) 2012 Zapier LLC).
//! The scan works bottom-up: lines are visited last-to-first, and a fragment
//! grows while successive lines share its quoted/header classification. A
//! blank line directly above a signature-looking line closes the fragment.
//!
//! Before the scan, a wrapped "On … wrote:" attribution is collapsed onto
//! one line: the first match's text, with its newlines removed, replaces the
//! match literally. Upstream used it as a Python `re.sub` replacement
//! template; the reference fixes that defect (see its module docstring).
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
pub fn read_fragments(text: &str) -> Vec<Fragment> {
    let text = collapse_wrapped_attribution(text);
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
    fragments
}

// --- the wrapped-attribution collapse -----------------------------------------

/// `_find_multi_quote_header`: the match of upstream's
/// `(?!On.*On\s.+?wrote:)(On\s(.+?)wrote:)` (`re.DOTALL`) in `chars`, found
/// in linear time, as `(p, r)`: the match is `chars[p..r + 6]`, starting
/// with `On\s` at `p` and ending with the `wrote:` at `r`.
///
/// The pattern matches `On\s` at a position `p`, then the first `wrote:`
/// starting at `p + 4` or later (`.+?` needs one character). Its lookahead
/// rejects `p` when another such `On\s` at `q >= p + 2` also has a `wrote:`
/// starting at `q + 4` or later. With `W` the start of the last `wrote:`,
/// the qualifying positions are the `On\s` positions `p <= W - 4`, and the
/// lookahead accepts only the last of them. That position is the only match,
/// both for the search and for every match the substitution would replace.
/// Run by a backtracking engine over a whole body, the lookahead is cubic in
/// the number of "On" + whitespace positions; the reference computes the
/// match the same way as here.
fn find_multi_quote_header(chars: &[char]) -> Option<(usize, usize)> {
    let n = chars.len();
    let wrote: Vec<char> = "wrote:".chars().collect();
    let wrote_at = |i: usize| i + 6 <= n && chars[i..i + 6] == wrote[..];
    let last_wrote = (0..n).rev().find(|&i| wrote_at(i))?;
    // The last "On\s" at or before last_wrote - 4.
    let p = (0..=last_wrote.checked_sub(4)?).rev().find(|&p| {
        p + 2 < n && chars[p] == 'O' && chars[p + 1] == 'n' && pystr::is_space(chars[p + 2])
    })?;
    // The first "wrote:" at p + 4 or later; last_wrote is one.
    let r = (p + 4..=last_wrote).find(|&r| wrote_at(r))?;
    Some((p, r))
}

/// `read_fragments`' collapse of a wrapped "On … wrote:" header onto one
/// line. Upstream ran `re.sub` with the pattern; it has exactly one match
/// (see [`find_multi_quote_header`]), so the match's text, with its newlines
/// removed, is spliced in place of the match. It is inserted literally, not
/// as a replacement template.
fn collapse_wrapped_attribution(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let Some((p, r)) = find_multi_quote_header(&chars) else {
        return text.to_string();
    };
    let mut out: String = chars[..p].iter().collect();
    out.extend(chars[p..r + 6].iter().filter(|&&c| c != '\n'));
    out.extend(&chars[r + 6..]);
    out
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

    #[test]
    fn collapse_is_literal() {
        let text = "a\nOn x\n\\1 \\g<1> \\U $1 wrote:\n> q";
        assert_eq!(
            collapse_wrapped_attribution(text),
            "a\nOn x\\1 \\g<1> \\U $1 wrote:\n> q"
        );
    }

    #[test]
    fn outlook_boundary() {
        assert_eq!(break_outlook_boundaries("a\n_______"), "a\n\n_______");
        assert_eq!(break_outlook_boundaries("a\n -------x"), "a\n\n -------x");
        assert_eq!(break_outlook_boundaries("a\n------"), "a\n------");
        assert_eq!(break_outlook_boundaries("\n\n_______"), "\n\n_______");
    }
}
