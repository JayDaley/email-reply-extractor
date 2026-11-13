//! Differential-testing driver for `rust/tools/differential.py`.
//!
//! Reads one JSON object per line on stdin, `{"fn": <name>, "args": {...}}`,
//! calls the named function, and writes one JSON line per input:
//! `{"result": <value>}`, or `{"error": true}` when an internal helper
//! signals an error. Names and argument encodings follow
//! `conformance/README.md`.
//!
//! ```text
//! cargo run --release --example difftest < cases.jsonl > results.jsonl
//! ```

use std::io::{self, BufRead, BufWriter, Write};

use email_reply_extractor::internal;
use email_reply_extractor::*;
use serde_json::{json, Value};

fn s<'a>(args: &'a Value, name: &str) -> &'a str {
    args[name].as_str().unwrap_or("")
}

fn opt<'a>(args: &'a Value, name: &str) -> Option<&'a str> {
    args.get(name).and_then(Value::as_str)
}

fn flag(args: &Value, name: &str, default: bool) -> bool {
    args.get(name).and_then(Value::as_bool).unwrap_or(default)
}

fn lines(args: &Value) -> Vec<&str> {
    args["lines"]
        .as_array()
        .map(|a| a.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default()
}

fn call(name: &str, a: &Value) -> Value {
    let result = match name {
        "extract_new_text" => {
            let r = extract_new_text(opt(a, "body"), opt(a, "parent_body"), opt(a, "html_body"));
            json!({"text": r.text, "method": r.method, "status": r.status.as_str()})
        }
        "clean_for_scoring" => {
            let r = clean_for_scoring(s(a, "extracted_text"), opt(a, "html_signature_text"));
            json!({"text": r.text, "ignored_lines": r.ignored_lines})
        }
        "strip_parent_content" => json!(strip_parent_content(
            s(a, "text"),
            s(a, "parent_body"),
            flag(a, "continuation", false)
        )),
        "html_to_text" => json!(html_to_text(s(a, "html"))),
        "split_html_parts" => {
            let p = split_html_parts(s(a, "html"));
            json!({"novel_text": p.novel_text, "quoted_text": p.quoted_text, "signature_text": p.signature_text})
        }
        "tolerant_lines" => json!(tolerant_lines(s(a, "text"))),
        "texts_equivalent" => json!(texts_equivalent(s(a, "a"), s(a, "b"))),
        "read_fragments" => match internal::read_fragments(s(a, "text")) {
            Ok(f) => Value::Array(
                f.iter()
                    .map(|f| json!({"content": f.content, "quoted": f.quoted}))
                    .collect(),
            ),
            Err(_) => return json!({"error": true}),
        },
        "is_attribution_line" => json!(internal::is_attribution_line(s(a, "line"))),
        "is_quote_line" => json!(internal::is_quote_line(s(a, "line"))),
        "is_signoff_name_line" => json!(internal::is_signoff_name_line(s(a, "text"))),
        "is_mobile_tagline" => json!(internal::is_mobile_tagline(s(a, "line"))),
        "is_signature_delimiter" => json!(internal::is_signature_delimiter(s(a, "line"))),
        "normalize_body" => json!(internal::normalize_body(s(a, "body"))),
        "custom_clean" => json!(internal::custom_clean(
            s(a, "text"),
            flag(a, "localized", true)
        )),
        "count_unquoted_content_lines" => json!(internal::count_unquoted_content_lines(
            s(a, "body"),
            flag(a, "localized", true)
        )),
        "find_quote_header_block" => json!(internal::find_quote_header_block(
            &lines(a),
            flag(a, "localized", true)
        )),
        "find_signoff_boundary" => json!(internal::find_signoff_boundary(&lines(a))),
        "strip_attribution_lines" => json!(internal::strip_attribution_lines(&lines(a))),
        "strip_after_original_message_divider" => json!(
            internal::strip_after_original_message_divider(&lines(a), flag(a, "localized", true))
        ),
        other => return json!({"unknown": other}),
    };
    json!({ "result": result })
}

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut out = BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let case: Value = serde_json::from_str(&line).expect("JSON case");
        let name = case["fn"].as_str().unwrap_or("");
        let reply = call(name, &case["args"]);
        serde_json::to_writer(&mut out, &reply)?;
        out.write_all(b"\n")?;
    }
    out.flush()
}
