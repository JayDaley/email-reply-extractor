//! The subset of Python's `html.parser.HTMLParser` the reference relies on.
//!
//! A port of CPython 3.14's `HTMLParser` tokenizer with
//! `convert_charrefs=True` and `scripting=False`, fed one document and then
//! closed, together with `html.unescape`. Only start tags, end tags and data
//! reach the [`Sink`]; comments, declarations, processing instructions and
//! CDATA sections are consumed and dropped, as the reference's subclasses do
//! not override their handlers.
//!
//! Positions are code point indices into a `Vec<char>`, as in Python.
//!
//! One Python failure mode is reproduced: `int()` refuses a decimal string of
//! more than 4300 digits, so `html.unescape` raises `ValueError` on a decimal
//! character reference that long. The reference catches the exception and
//! keeps what was handled before it; [`parse`] stops at the same point.

use crate::html_entities::HTML5;
use crate::pystr;

/// Receives the parser's events.
pub(crate) trait Sink {
    fn start_tag(&mut self, tag: &str, attrs: &[(String, Option<String>)]);
    fn end_tag(&mut self, tag: &str);
    fn data(&mut self, data: &str);
}

/// `html.unescape` (and so the parser) raised `ValueError`.
#[derive(Debug)]
pub(crate) struct Abort;

/// Python's default `sys.int_info.default_max_str_digits`.
const MAX_STR_DIGITS: usize = 4300;

const CDATA_CONTENT_ELEMENTS: [&str; 6] =
    ["script", "style", "xmp", "iframe", "noembed", "noframes"];
const RCDATA_CONTENT_ELEMENTS: [&str; 2] = ["textarea", "title"];

fn is_tag_ws(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\r' | '\u{c}' | ' ')
}

fn lookup(name: &str) -> Option<&'static str> {
    HTML5
        .binary_search_by(|(k, _)| (*k).cmp(name))
        .ok()
        .map(|i| HTML5[i].1)
}

/// The windows-1252 remapping of `html._invalid_charrefs`.
fn invalid_charref(num: u32) -> Option<char> {
    let c = match num {
        0x00 => '\u{fffd}',
        0x0d => '\r',
        0x80 => '\u{20ac}',
        0x81 => '\u{81}',
        0x82 => '\u{201a}',
        0x83 => '\u{0192}',
        0x84 => '\u{201e}',
        0x85 => '\u{2026}',
        0x86 => '\u{2020}',
        0x87 => '\u{2021}',
        0x88 => '\u{02c6}',
        0x89 => '\u{2030}',
        0x8a => '\u{0160}',
        0x8b => '\u{2039}',
        0x8c => '\u{0152}',
        0x8d => '\u{8d}',
        0x8e => '\u{017d}',
        0x8f => '\u{8f}',
        0x90 => '\u{90}',
        0x91 => '\u{2018}',
        0x92 => '\u{2019}',
        0x93 => '\u{201c}',
        0x94 => '\u{201d}',
        0x95 => '\u{2022}',
        0x96 => '\u{2013}',
        0x97 => '\u{2014}',
        0x98 => '\u{02dc}',
        0x99 => '\u{2122}',
        0x9a => '\u{0161}',
        0x9b => '\u{203a}',
        0x9c => '\u{0153}',
        0x9d => '\u{9d}',
        0x9e => '\u{017e}',
        0x9f => '\u{0178}',
        _ => return None,
    };
    Some(c)
}

/// `html._invalid_codepoints`.
fn is_invalid_codepoint(num: u32) -> bool {
    matches!(num, 0x1..=0x8 | 0xb | 0xe..=0x1f | 0x7f..=0x9f | 0xfdd0..=0xfdef)
        || (num & 0xfffe == 0xfffe && num <= 0x10ffff)
}

/// The replacement for a numeric reference given its digits (no `&#`, no `;`).
fn numeric(digits: &[char], hex: bool) -> Result<String, Abort> {
    // Python converts with int(); only the value's range matters below.
    let significant: Vec<char> = digits.iter().copied().skip_while(|&c| c == '0').collect();
    if !hex && digits.len() > MAX_STR_DIGITS {
        return Err(Abort);
    }
    let num: u32 = if significant.len() > 8 {
        u32::MAX
    } else if significant.is_empty() {
        0
    } else {
        let s: String = significant.iter().collect();
        u32::from_str_radix(&s, if hex { 16 } else { 10 }).unwrap_or(u32::MAX)
    };
    if let Some(c) = invalid_charref(num) {
        return Ok(c.to_string());
    }
    if (0xD800..=0xDFFF).contains(&num) || num > 0x10FFFF {
        return Ok("\u{fffd}".to_string());
    }
    if is_invalid_codepoint(num) {
        return Ok(String::new());
    }
    Ok(char::from_u32(num)
        .map(|c| c.to_string())
        .unwrap_or_default())
}

/// `html.unescape`.
pub(crate) fn unescape(s: &str) -> Result<String, Abort> {
    if !s.contains('&') {
        return Ok(s.to_string());
    }
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < n {
        if chars[i] != '&' {
            out.push(chars[i]);
            i += 1;
            continue;
        }
        // _charref: &(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)
        let p = i + 1;
        if p < n && chars[p] == '#' {
            let mut q = p + 1;
            while q < n && chars[q].is_ascii_digit() {
                q += 1;
            }
            if q > p + 1 {
                let rep = numeric(&chars[p + 1..q], false)?;
                out.push_str(&rep);
                i = if q < n && chars[q] == ';' { q + 1 } else { q };
                continue;
            }
            if p + 1 < n && (chars[p + 1] == 'x' || chars[p + 1] == 'X') {
                let mut q = p + 2;
                while q < n && chars[q].is_ascii_hexdigit() {
                    q += 1;
                }
                if q > p + 2 {
                    let rep = numeric(&chars[p + 2..q], true)?;
                    out.push_str(&rep);
                    i = if q < n && chars[q] == ';' { q + 1 } else { q };
                    continue;
                }
            }
            // '#' is excluded from the named alternative.
            out.push('&');
            i += 1;
            continue;
        }
        let mut q = p;
        while q < n
            && q - p < 32
            && !matches!(
                chars[q],
                '\t' | '\n' | '\u{c}' | ' ' | '<' | '&' | '#' | ';'
            )
        {
            q += 1;
        }
        if q == p {
            out.push('&');
            i += 1;
            continue;
        }
        if q < n && chars[q] == ';' {
            q += 1;
        }
        let name: String = chars[p..q].iter().collect();
        out.push_str(&named(&name, &chars[p..q]));
        i = q;
    }
    Ok(out)
}

/// `_replace_charref` for a named reference `s` (without the `&`).
fn named(s: &str, chars: &[char]) -> String {
    if let Some(v) = lookup(s) {
        return v.to_string();
    }
    for x in (2..chars.len()).rev() {
        let prefix: String = chars[..x].iter().collect();
        if let Some(v) = lookup(&prefix) {
            let rest: String = chars[x..].iter().collect();
            return format!("{v}{rest}");
        }
    }
    format!("&{s}")
}

/// `html.parser._unescape_attrvalue`: attr_charref.sub(_replace_attr_charref).
fn unescape_attr(s: &str) -> Result<String, Abort> {
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut out = String::new();
    let mut i = 0;
    while i < n {
        if chars[i] != '&' {
            out.push(chars[i]);
            i += 1;
            continue;
        }
        // &(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*)[;=]?
        let p = i + 1;
        let mut end = None;
        let mut numeric_ref = false;
        if p < n && chars[p] == '#' {
            let mut q = p + 1;
            while q < n && chars[q].is_ascii_digit() {
                q += 1;
            }
            if q > p + 1 {
                end = Some(q);
            } else if p + 1 < n && (chars[p + 1] == 'x' || chars[p + 1] == 'X') {
                let mut q = p + 2;
                while q < n && chars[q].is_ascii_hexdigit() {
                    q += 1;
                }
                if q > p + 2 {
                    end = Some(q);
                }
            }
            numeric_ref = true;
        } else if p < n && chars[p].is_ascii_alphabetic() {
            let mut q = p + 1;
            while q < n && chars[q].is_ascii_alphanumeric() {
                q += 1;
            }
            end = Some(q);
        }
        let Some(mut q) = end else {
            out.push('&');
            i += 1;
            continue;
        };
        if q < n && (chars[q] == ';' || chars[q] == '=') {
            q += 1;
        }
        let reference: String = chars[i..q].iter().collect();
        // Numeric references are always unescaped; named ones only on an
        // exact match not followed by '='.
        if numeric_ref || (!reference.ends_with('=') && lookup(&reference[1..]).is_some()) {
            out.push_str(&unescape(&reference)?);
        } else {
            out.push_str(&reference);
        }
        i = q;
    }
    Ok(out)
}

// --- the tokenizer's patterns, matched by hand -----------------------------------

/// The optional value part shared by `attrfind_tolerant` and `locatetagend`:
/// `[\t\n\r\f ]*=[\t\n\r\f ]*('[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*)`,
/// with the regex engine's backtracking into the whitespace after `=`.
/// Returns `(end, value_start, value_end)`.
fn value_part(s: &[char], pos: usize) -> Option<(usize, usize, usize)> {
    let n = s.len();
    let mut q = pos;
    while q < n && is_tag_ws(s[q]) {
        q += 1;
    }
    if q >= n || s[q] != '=' {
        return None;
    }
    let r0 = q + 1;
    let mut r1 = r0;
    while r1 < n && is_tag_ws(s[r1]) {
        r1 += 1;
    }
    if r1 < n && (s[r1] == '\'' || s[r1] == '"') {
        let quote = s[r1];
        if let Some(off) = s[r1 + 1..].iter().position(|&c| c == quote) {
            let end = r1 + 1 + off + 1;
            return Some((end, r1, end));
        }
        // Unclosed quote: backtrack one whitespace character, where the bare
        // alternative matches the empty string.
        if r1 > r0 {
            return Some((r1 - 1, r1 - 1, r1 - 1));
        }
        return None;
    }
    let mut t = r1;
    while t < n && !(s[t] == '>' || is_tag_ws(s[t])) {
        t += 1;
    }
    Some((t, r1, t))
}

fn lookbehind_ok(s: &[char], k: usize) -> bool {
    k > 0
        && matches!(
            s[k - 1],
            '\'' | '"' | '\t' | '\n' | '\r' | '\u{c}' | ' ' | '/'
        )
}

/// The attribute name: `[^\t\n\r\f />][^\t\n\r\f /=>]*`. Returns its end.
fn attr_name(s: &[char], k: usize) -> Option<usize> {
    if k >= s.len() || is_tag_ws(s[k]) || s[k] == '/' || s[k] == '>' {
        return None;
    }
    let mut q = k + 1;
    while q < s.len() && !(is_tag_ws(s[q]) || matches!(s[q], '/' | '=' | '>')) {
        q += 1;
    }
    Some(q)
}

/// `(?:[\t\n\r\f ]|/(?!>))*`
fn skip_ws_slash(s: &[char], mut q: usize) -> usize {
    while q < s.len() && (is_tag_ws(s[q]) || (s[q] == '/' && s.get(q + 1) != Some(&'>'))) {
        q += 1;
    }
    q
}

/// `tagfind_tolerant.match(s, p)`: `([a-zA-Z][^\t\n\r\f />]*)(?:[\t\n\r\f ]|/(?!>))*`.
/// The caller guarantees an ASCII letter at `p`. Returns `(name_end, end)`.
fn tagfind(s: &[char], p: usize) -> (usize, usize) {
    let mut q = p + 1;
    while q < s.len() && !(is_tag_ws(s[q]) || s[q] == '/' || s[q] == '>') {
        q += 1;
    }
    (q, skip_ws_slash(s, q))
}

struct Attr {
    name_start: usize,
    name_end: usize,
    value: Option<(usize, usize)>,
    end: usize,
}

/// `attrfind_tolerant.match(s, k)`.
fn attrfind(s: &[char], k: usize) -> Option<Attr> {
    if !lookbehind_ok(s, k) {
        return None;
    }
    let name_end = attr_name(s, k)?;
    let (after, value) = match value_part(s, name_end) {
        Some((end, vs, ve)) => (end, Some((vs, ve))),
        None => (name_end, None),
    };
    Some(Attr {
        name_start: k,
        name_end,
        value,
        end: skip_ws_slash(s, after),
    })
}

/// `locatetagend.match(s, p)`; the caller guarantees an ASCII letter at `p`.
fn locatetagend(s: &[char], p: usize) -> usize {
    let n = s.len();
    let skip = |mut q: usize| {
        while q < n && (is_tag_ws(s[q]) || s[q] == '/') {
            q += 1;
        }
        q
    };
    let mut q = p + 1;
    while q < n && !(is_tag_ws(s[q]) || s[q] == '/' || s[q] == '>') {
        q += 1;
    }
    q = skip(q);
    loop {
        if !lookbehind_ok(s, q) {
            break;
        }
        let Some(name_end) = attr_name(s, q) else {
            break;
        };
        q = match value_part(s, name_end) {
            Some((end, _, _)) => end,
            None => name_end,
        };
        q = skip(q);
    }
    if q < n && s[q] == '>' {
        q += 1;
    }
    q
}

fn starts_with(s: &[char], i: usize, prefix: &str) -> bool {
    (i..)
        .zip(prefix.chars())
        .all(|(j, c)| j < s.len() && s[j] == c)
}

fn find(s: &[char], from: usize, needle: &str) -> Option<usize> {
    let pat: Vec<char> = needle.chars().collect();
    if from > s.len() {
        return None;
    }
    (from..=s.len().saturating_sub(pat.len()))
        .find(|&i| i + pat.len() <= s.len() && s[i..i + pat.len()] == pat[..])
}

fn text(s: &[char], a: usize, b: usize) -> String {
    s[a..b].iter().collect()
}

struct Parser<'s, S: Sink> {
    raw: &'s [char],
    sink: &'s mut S,
    cdata_elem: Option<String>,
    escapable: bool,
}

impl<S: Sink> Parser<'_, S> {
    fn data(&mut self, a: usize, b: usize, unescape_it: bool) -> Result<(), Abort> {
        let t = text(self.raw, a, b);
        if unescape_it {
            self.sink.data(&unescape(&t)?);
        } else {
            self.sink.data(&t);
        }
        Ok(())
    }

    /// The CDATA-mode `interesting` search: `</elem(?=[\t\n\r\f />])`,
    /// ASCII case-insensitive (or the end of the input for `plaintext`).
    fn cdata_search(&self, from: usize, elem: &str) -> Option<usize> {
        let s = self.raw;
        if elem == "plaintext" {
            return Some(s.len());
        }
        let name: Vec<char> = elem.chars().collect();
        let m = name.len();
        (from..s.len()).find(|&i| {
            s[i] == '<'
                && i + 1 < s.len()
                && s[i + 1] == '/'
                && i + 2 + m < s.len()
                && s[i + 2..i + 2 + m]
                    .iter()
                    .zip(&name)
                    .all(|(a, b)| a.eq_ignore_ascii_case(b))
                && (is_tag_ws(s[i + 2 + m]) || s[i + 2 + m] == '/' || s[i + 2 + m] == '>')
        })
    }

    /// `goahead(1)`. A single call with `end` true reproduces `feed()` then
    /// `close()`: everything `feed` leaves unprocessed is handled the same
    /// way by `close`.
    fn run(&mut self) -> Result<(), Abort> {
        let raw = self.raw;
        let n = raw.len();
        let mut i = 0;
        while i < n {
            let j;
            if let Some(elem) = self.cdata_elem.clone() {
                match self.cdata_search(i, &elem) {
                    Some(pos) => j = pos,
                    None => break,
                }
            } else {
                j = (i..n).find(|&k| raw[k] == '<').unwrap_or(n);
            }
            if i < j {
                self.data(i, j, self.escapable)?;
            }
            i = j;
            if i == n {
                break;
            }
            // raw[i] == '<'
            let k: isize = if i + 1 < n && raw[i + 1].is_ascii_alphabetic() {
                self.parse_starttag(i)?
            } else if starts_with(raw, i, "</") {
                self.parse_endtag(i)
            } else if starts_with(raw, i, "<!--") {
                parse_comment(raw, i)
            } else if starts_with(raw, i, "<?") {
                find(raw, i + 2, ">").map_or(-1, |p| (p + 1) as isize)
            } else if starts_with(raw, i, "<!") {
                parse_html_declaration(raw, i)
            } else {
                self.sink.data("<");
                (i + 1) as isize
            };
            if k < 0 {
                // end is true: an unterminated construct runs to the end.
                if starts_with(raw, i, "</") && i + 2 == n {
                    self.sink.data("</");
                }
                i = n;
            } else {
                i = k as usize;
            }
        }
        if i < n {
            self.data(i, n, self.escapable)?;
        }
        Ok(())
    }

    fn parse_starttag(&mut self, i: usize) -> Result<isize, Abort> {
        let raw = self.raw;
        let endpos = locatetagend(raw, i + 1);
        if raw[endpos - 1] != '>' {
            return Ok(-1);
        }
        let (name_end, mut k) = tagfind(raw, i + 1);
        let tag = pystr::lower(&text(raw, i + 1, name_end));
        let mut attrs: Vec<(String, Option<String>)> = Vec::new();
        while k < endpos {
            let Some(m) = attrfind(raw, k) else {
                break;
            };
            let name = pystr::lower(&text(raw, m.name_start, m.name_end));
            let value = match m.value {
                None => None,
                Some((vs, ve)) => {
                    let mut v = text(raw, vs, ve);
                    let quoted = (v.starts_with('\'') && v.ends_with('\'')
                        || v.starts_with('"') && v.ends_with('"'))
                        && !v.is_empty();
                    if quoted {
                        let cs: Vec<char> = v.chars().collect();
                        v = if cs.len() >= 2 {
                            cs[1..cs.len() - 1].iter().collect()
                        } else {
                            String::new()
                        };
                    }
                    if !v.is_empty() {
                        v = unescape_attr(&v)?;
                    }
                    Some(v)
                }
            };
            attrs.push((name, value));
            k = m.end;
        }
        let end_text = text(raw, k.min(endpos), endpos);
        let end = pystr::strip(&end_text);
        if end != ">" && end != "/>" {
            self.data(i, endpos, false)?;
            return Ok(endpos as isize);
        }
        if end.ends_with("/>") {
            self.sink.start_tag(&tag, &attrs);
            self.sink.end_tag(&tag);
        } else {
            self.sink.start_tag(&tag, &attrs);
            if CDATA_CONTENT_ELEMENTS.contains(&tag.as_str()) || tag == "plaintext" {
                self.cdata_elem = Some(tag);
                self.escapable = false;
            } else if RCDATA_CONTENT_ELEMENTS.contains(&tag.as_str()) {
                self.cdata_elem = Some(tag);
                self.escapable = true;
            }
        }
        Ok(endpos as isize)
    }

    fn parse_endtag(&mut self, i: usize) -> isize {
        let raw = self.raw;
        if find(raw, i + 2, ">").is_none() {
            return -1;
        }
        if !(i + 2 < raw.len() && raw[i + 2].is_ascii_alphabetic()) {
            if raw.get(i + 2) == Some(&'>') {
                return (i + 3) as isize;
            }
            return parse_bogus_comment(raw, i);
        }
        let j = locatetagend(raw, i + 2);
        if raw[j - 1] != '>' {
            return -1;
        }
        let (name_end, _) = tagfind(raw, i + 2);
        let tag = pystr::lower(&text(raw, i + 2, name_end));
        self.sink.end_tag(&tag);
        self.cdata_elem = None;
        self.escapable = true;
        j as isize
    }
}

fn parse_comment(raw: &[char], i: usize) -> isize {
    // commentclose: --!?> searched from i+4
    let n = raw.len();
    let mut p = i + 4;
    while p + 1 < n {
        if raw[p] == '-' && raw[p + 1] == '-' {
            if p + 2 < n && raw[p + 2] == '>' {
                return (p + 3) as isize;
            }
            if p + 3 < n && raw[p + 2] == '!' && raw[p + 3] == '>' {
                return (p + 4) as isize;
            }
        }
        p += 1;
    }
    // commentabruptclose: -?> matched at i+4
    let q = i + 4;
    if q < n && raw[q] == '>' {
        return (q + 1) as isize;
    }
    if q + 1 < n && raw[q] == '-' && raw[q + 1] == '>' {
        return (q + 2) as isize;
    }
    -1
}

fn parse_bogus_comment(raw: &[char], i: usize) -> isize {
    find(raw, i + 2, ">").map_or(-1, |p| (p + 1) as isize)
}

fn parse_html_declaration(raw: &[char], i: usize) -> isize {
    if starts_with(raw, i, "<![CDATA[") {
        return find(raw, i + 9, "]]>").map_or(-1, |j| (j + 3) as isize);
    }
    let head: String = raw[i..(i + 9).min(raw.len())].iter().collect();
    if pystr::lower(&head) == "<!doctype" {
        return find(raw, i + 9, ">").map_or(-1, |p| (p + 1) as isize);
    }
    parse_bogus_comment(raw, i)
}

/// Feed `html` to a fresh parser and close it, sending events to `sink`.
/// `Err` when Python's parser would have raised; the events sent before that
/// point stand.
pub(crate) fn parse<S: Sink>(html: &str, sink: &mut S) -> Result<(), Abort> {
    let raw: Vec<char> = html.chars().collect();
    let mut parser = Parser {
        raw: &raw,
        sink,
        cdata_elem: None,
        escapable: true,
    };
    parser.run()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unescape_rules() {
        let u = |s: &str| unescape(s).unwrap();
        assert_eq!(u("&amp;&lt;"), "&<");
        assert_eq!(u("&amp"), "&");
        assert_eq!(u("&noti;"), "\u{ac}i;");
        assert_eq!(u("&#x80;"), "\u{20ac}");
        assert_eq!(u("&#0;"), "\u{fffd}");
        assert_eq!(u("&#xD800;"), "\u{fffd}");
        assert_eq!(u("&#1;"), "");
        assert_eq!(u("&#99999999999999999;"), "\u{fffd}");
        assert_eq!(u("&#;"), "&#;");
        assert_eq!(u("&bogus;"), "&bogus;");
        assert!(unescape(&format!("&#{}", "1".repeat(4301))).is_err());
        assert!(unescape(&format!("&#{}", "1".repeat(4300))).is_ok());
    }
}
