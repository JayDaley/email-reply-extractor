//! Worst-case running time: adversarial inputs complete in linear time.
//!
//! The Rust counterpart of the reference's `tests/test_complexity.py`. Each
//! test feeds one input that drove a superlinear path of an earlier
//! implementation (regex backtracking or a quadratic scan) and asserts that
//! the call completes within [`BOUND`]. Where the earlier Rust port was
//! superlinear on the input, it is sized so that the earlier port needed well
//! over a minute in a release build; the current one needs a small fraction of
//! the bound even in a debug build. Some findings of the reference did not
//! reproduce in this port, whose `regex` crate runs the affected patterns in
//! linear time; their cases are kept, at a size the debug build handles
//! quickly, so that the three ports pin the same inputs.
//!
//! The tests pin running time, not output: equivalence of the output with the
//! earlier implementation was established by differential testing when the
//! paths were rewritten, except for the parent-diff aligned-run rule, whose
//! output changed by design in generation 7 (`docs/generation-7.md`). The
//! remaining tests check that the linear-time helpers handle multi-byte
//! characters next to the delimiters they slice at (results as the reference
//! returns them).

use std::time::{Duration, Instant};

use email_reply_extractor::internal::{
    custom_clean, find_quote_header_block, is_attribution_line, read_fragments,
    strip_after_original_message_divider, strip_attribution_lines,
};
use email_reply_extractor::{clean_for_scoring, extract_new_text, split_html_parts};
use email_reply_extractor::{strip_parent_content, Status};

/// Time each call may take.
const BOUND: Duration = Duration::from_secs(5);

fn bounded<T>(call: impl FnOnce() -> T) -> T {
    let start = Instant::now();
    let result = call();
    let elapsed = start.elapsed();
    assert!(elapsed < BOUND, "took {elapsed:?}");
    result
}

fn body(s: &str) -> Option<&str> {
    Some(s)
}

/// A child whose `n` short lines chain backward, one at a time, off three
/// parent-diff seeds, and the parent that proves every link (rule (c)).
fn seed_chain(n: usize) -> (String, String) {
    let seeds = [
        "s1 s2 s3 s4 s5 s6 s7 s8",
        "t1 t2 t3 t4 t5 t6 t7 t8",
        "u1 u2 u3 u4 u5 u6 u7 u8",
    ];
    let mut child = vec!["a"; n];
    child.extend(seeds);
    let parent = format!("a a\na {}", seeds.join("\n"));
    (child.join("\n"), parent)
}

/// A child and a parent of `n` lines of 30 words each, no word shared: every
/// child line is a rewrap-rule (b) query that is not in the parent.
fn distinct_long_lines(n: usize) -> (String, String) {
    let side = |tag: char| {
        (0..n)
            .map(|k| {
                (0..30)
                    .map(|j| format!("{tag}{k}x{j}"))
                    .collect::<Vec<_>>()
                    .join(" ")
            })
            .collect::<Vec<_>>()
            .join("\n")
    };
    (side('c'), side('p'))
}

/// A re-wrapped paragraph: three 9-word seed lines, then `n` 3-word remainder
/// lines that the continuation rule (c) chains off the seeds, each join a
/// query against a parent of 10-word lines.
fn remainder_chain(n: usize) -> (String, String) {
    let words: Vec<String> = (0..3 * n + 27).map(|k| format!("w{k}")).collect();
    let parent: Vec<String> = words.chunks(10).map(|c| c.join(" ")).collect();
    let mut child: Vec<String> = words[..27].chunks(9).map(|c| c.join(" ")).collect();
    child.extend(words[27..].chunks(3).map(|c| c.join(" ")));
    (child.join("\n"), parent.join("\n"))
}

/// A body with `n` tagged lines below a French quote header, and a parent
/// that uses every tag: each tag is tested against the parent and the text
/// above the block, and none belongs to the author.
fn localized_tags(n: usize) -> (String, String) {
    let quoted = "The quoted message carries enough words to be matched in the parent.";
    let tags: String = (0..n).map(|k| format!("[Q{k}] ok\n")).collect();
    let body = format!(
        "Thanks for the review.\n{}\nCheers,\nMed\n\
         De : Someone <someone@example.org>\n\
         Envoy\u{e9} : lundi 1 janvier 2026 10:00\n\
         \u{c0} : list@example.org\n\
         Objet : Re: review\n{quoted}\n{tags}",
        "zz ".repeat(100000)
    );
    let parent_tags: Vec<String> = (0..n).map(|k| format!("[Q{k}]")).collect();
    let parent = format!(
        "{}\n{quoted}\n{}",
        "zz ".repeat(100000),
        parent_tags.join(" ")
    );
    (body, parent)
}

// --- regex findings (linear in this port's `regex` crate; kept for alignment) ---

#[test]
fn a1_multi_quote_header() {
    // The reference's lookahead was cubic (800 repeats took 1.2 s).
    bounded(|| read_fragments(&"On ".repeat(4000)));
    bounded(|| extract_new_text(body(&format!("hello\n{}", "On ".repeat(4000))), None, None));
}

#[test]
fn a8_attribution_collapse_template() {
    // The earlier implementations used the collapsed header as a replacement
    // template; every "\1" expanded to the whole match, so the output was
    // quadratic in size (4,000 repeats built 32 MB).
    let header = format!("On {} wrote:", "\\1".repeat(60000));
    bounded(|| read_fragments(&header));
    bounded(|| extract_new_text(body(&format!("hello\n{header}\n> q")), None, None));
}

#[test]
fn a2_address_attribution() {
    bounded(|| is_attribution_line(&format!("x{}", "<a@".repeat(6400))));
    bounded(|| extract_new_text(body(&format!("hello\n{}", "<a@".repeat(6400))), None, None));
}

#[test]
fn a3_german_attribution() {
    let line = format!("Am {}x", "schrieb: ".repeat(20000));
    bounded(|| is_attribution_line(&line));
    let wrapped = format!("{}x", "schrieb: ".repeat(20000));
    bounded(|| strip_attribution_lines(&["Am x", &wrapped]));
    bounded(|| extract_new_text(body(&format!("hello\n{line}")), None, None));
}

#[test]
fn a4_original_message_divider() {
    // The reference took 14 s on 10,000 dashes.
    let text = format!("hello\n{}\nbye", "-".repeat(40000));
    bounded(|| extract_new_text(body(&text), None, None));
    let rule = "-".repeat(40000);
    bounded(|| strip_after_original_message_divider(&[&rule], true));
}

#[test]
fn a5_signoff_line_tail() {
    let text = format!("hello\nbest{}x", " ".repeat(150000));
    bounded(|| extract_new_text(body(&text), None, None));
}

#[test]
fn a6_contact_line() {
    bounded(|| clean_for_scoring(&format!("hello\nDr x{}", "<a@".repeat(6400)), None));
}

#[test]
fn a7_list_footer() {
    bounded(|| clean_for_scoring(&format!("hello\n{}", "mailing list ".repeat(8000)), None));
}

#[test]
fn e2_custom_signature_divider() {
    bounded(|| clean_for_scoring(&format!("hello\n{}", "\n===\n".repeat(20000)), None));
}

#[test]
fn custom_clean_leading_blanks() {
    let text = format!("{}hello", "\n".repeat(300_000));
    assert_eq!(bounded(|| custom_clean(&text, true)), "hello");
}

// --- findings that reproduced in this port ------------------------------------

#[test]
fn a5_greeting_tail() {
    // A `fancy-regex` pattern here: 20,000 blanks took 5.4 s in the earlier
    // port (release build), and the cost is quadratic.
    let text = format!("hi{}x", " ".repeat(150000));
    bounded(|| clean_for_scoring(&text, None));
}

#[test]
fn b1_stray_close_tags() {
    // A stray close tag scanned the whole open-element stack: 40,000 pairs
    // took 8.9 s in the earlier port.
    let html = format!("{}{}", "<a>".repeat(150000), "</b>".repeat(150000));
    bounded(|| split_html_parts(&html));
}

#[test]
fn b2_blockquotes_in_gmail_wrappers() {
    // Every <blockquote> walked the whole stack: 40,000 pairs took 16.5 s.
    let html = format!(
        "{}{}",
        "<div class=gmail_quote>".repeat(80000),
        "<blockquote>".repeat(80000)
    );
    bounded(|| split_html_parts(&html));
}

#[test]
fn c4_continuation_chain() {
    // One full pass per backward-chained mark.
    let (child, parent) = seed_chain(80000);
    assert_eq!(bounded(|| strip_parent_content(&child, &parent, true)), "");
}

#[test]
fn c4_seed_chain_marks_every_line() {
    // The C4 input exercises the rule it is meant to: every line chains.
    let (child, parent) = seed_chain(50);
    assert_eq!(strip_parent_content(&child, &parent, true), "");
}

// --- generation 7: the parent-diff assist and the author-tag test ------------
//
// The inputs are built before the clock starts. Comments give the cost of
// the generation-6 port (release build) on the same input; where it was
// superlinear, the input is smaller than the reference's, as above.

#[test]
fn g7a_aligned_runs_of_identical_lines() {
    // Rule (a): difflib.SequenceMatcher, quadratic in the number of lines
    // (3.8 s at 10,000 lines, quadratic: about 60 s here).
    let text = "a\n".repeat(40000);
    bounded(|| strip_parent_content(&text, &text, false));
}

#[test]
fn g7b_distinct_long_lines() {
    // Rule (b): one substring search of the parent per child line (89 s at
    // 20,000 lines, quadratic: about 14 s here; the reference uses 60,000).
    // At 20,000 lines the debug build took 5.0 s on a CI runner.
    let (child, parent) = distinct_long_lines(8000);
    bounded(|| strip_parent_content(&child, &parent, false));
}

#[test]
fn g7b_automaton_queries() {
    // Rule (b) queries that only the suffix automaton decides: every interior
    // word occurs in the parent, which never contains the line. The earlier
    // port's `str::contains` took 12.6 s on the reference's 1,000,000 lines;
    // the case is kept at 250,000.
    let child = "a a a a a a a b\n".repeat(250_000);
    let parent = "a ".repeat(200_000);
    bounded(|| strip_parent_content(&child, &parent, false));
}

#[test]
fn g7c_remainder_chain() {
    // Rule (c): one substring search of the parent per join (96 s at
    // 100,000 remainder lines; the reference uses 300,000).
    let (child, parent) = remainder_chain(100_000);
    bounded(|| strip_parent_content(&child, &parent, true));
}

#[test]
fn g7tag_localized_tags() {
    // Inline-reply author tags: per tag, a search of the parent and of the
    // text above the quote header. The reference took 99 s; the earlier port
    // 0.64 s (its substring search is linear in the text).
    let (body, parent) = localized_tags(25000);
    bounded(|| extract_new_text(Some(&body), Some(&parent), None));
}

#[test]
fn remainder_chain_marks_every_line() {
    // The G7c input exercises the continuation rule: every remainder chains.
    let (child, parent) = remainder_chain(50);
    assert_eq!(strip_parent_content(&child, &parent, true), "");
}

#[test]
fn localized_tags_reach_every_tag() {
    // The G7tag input keeps the localized treatment: no tag is the author's,
    // so the quote header truncates and only the text above it is left.
    let (body, parent) = localized_tags(50);
    let result = extract_new_text(Some(&body), Some(&parent), None);
    assert!(result.text.ends_with("Cheers,\nMed"));
    assert!(!result.text.contains("[Q"));
}

#[test]
fn d1_quote_header_walk() {
    // The walk from every From: line over one long field run: 16,000 lines
    // took 25 s in the earlier port.
    let lines = vec!["From: a@b x"; 32000];
    bounded(|| find_quote_header_block(&lines, true));
    let text = format!("hello\n{}", "From: a@b x\n".repeat(32000));
    bounded(|| extract_new_text(body(&text), None, None));
}

#[test]
fn d2_pasted_header_run() {
    // The walk up a long field run from every From: line: 8,000 + 8,000 lines
    // took 9.9 s in the earlier port.
    let text = format!(
        "hello\n\n{}{}",
        "X: y\n".repeat(24000),
        "From: z\n".repeat(24000)
    );
    bounded(|| extract_new_text(body(&text), None, None));
}

#[test]
fn e1_trailing_signoff_debris() {
    // The whole tail reclassified per name line: 4,000 lines took 22 s.
    bounded(|| clean_for_scoring(&format!("hello\n{}", "A.io\n".repeat(10000)), None));
}

// --- multi-byte characters next to the delimiters the helpers slice at -------

#[test]
fn bracketed_address_next_to_multibyte() {
    // The `@` must have one character on each side; the bound `lt + 2` falls
    // inside a four-byte character here.
    assert!(is_attribution_line("<\u{1f600}@b> wrote:"));
    assert!(!is_attribution_line("<@\u{1f600}> wrote:"));
    assert!(is_attribution_line("<a@\u{1f600}> wrote:"));
    assert!(is_attribution_line("x <\u{e9}@\u{e9}> wrote:"));
    assert!(is_attribution_line("\u{1f600}<a@b> wrote:"));
    let r = clean_for_scoring("Body.\n\nDr x <\u{e9}@\u{1f600}>", None);
    assert_eq!((r.text.as_str(), r.ignored_lines), ("Body.", vec![2]));
    let r = clean_for_scoring("Body.\n\nDr x <@\u{1f600}>", None);
    assert_eq!(r.ignored_lines, Vec::<usize>::new());
}

#[test]
fn schrieb_and_mailing_list_next_to_multibyte() {
    assert!(is_attribution_line("Am \u{1f600} schrieb \u{1f600}:"));
    assert_eq!(
        strip_attribution_lines(&["Am x", "\u{1f600} schrieb \u{1f600}:", "y"]),
        vec!["y"]
    );
    let r = clean_for_scoring("Body.\n\n\u{1f600} mailing list \u{1f600} -- x", None);
    assert_eq!((r.text.as_str(), r.ignored_lines), ("Body.", vec![2]));
    let r = extract_new_text(body("Hi\n\u{1f600}--Original Message--\nq"), None, None);
    assert_eq!((r.text.as_str(), r.status), ("Hi\n\u{1f600}", Status::Ok));
}
