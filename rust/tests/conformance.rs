//! The shared conformance suite (`../conformance`, format in its README):
//! every public vector, every internal vector, every recorded output of the
//! fixture corpus, and the corpus digest.
//!
//! The data is read from the repository checkout; it is not part of the
//! packaged crate.

use std::fs;
use std::path::{Path, PathBuf};

use email_reply_extractor::internal;
use email_reply_extractor::*;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

fn conformance_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../conformance")
}

fn load(path: &Path) -> Value {
    let text = fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let value: Value = serde_json::from_str(&text).expect("valid JSON");
    assert_eq!(
        value["format"],
        json!(1),
        "{}: unknown format",
        path.display()
    );
    value
}

fn vector_files(dir: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = fs::read_dir(dir)
        .expect("vector directory")
        .map(|e| e.expect("entry").path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    files.sort();
    files
}

/// The arguments of one vector, with absent parameters taking their default.
struct Args<'a> {
    args: &'a Value,
    params: &'a [Value],
}

impl Args<'_> {
    fn get(&self, name: &str) -> &Value {
        if let Some(v) = self.args.get(name) {
            return v;
        }
        let param = self
            .params
            .iter()
            .find(|p| p["name"] == name)
            .unwrap_or_else(|| panic!("no parameter {name}"));
        param
            .get("default")
            .unwrap_or_else(|| panic!("{name} is required"))
    }
    fn str(&self, name: &str) -> &str {
        self.get(name)
            .as_str()
            .unwrap_or_else(|| panic!("{name} is not a string"))
    }
    fn opt(&self, name: &str) -> Option<&str> {
        self.get(name).as_str()
    }
    fn bool(&self, name: &str) -> bool {
        self.get(name).as_bool().expect("bool")
    }
    fn lines(&self, name: &str) -> Vec<&str> {
        self.get(name)
            .as_array()
            .expect("array")
            .iter()
            .map(|v| v.as_str().expect("string"))
            .collect()
    }
}

fn extraction_json(r: &ExtractionResult) -> Value {
    json!({"text": r.text, "method": r.method, "status": r.status.as_str()})
}

fn clean_json(r: &CleanResult) -> Value {
    json!({"text": r.text, "ignored_lines": r.ignored_lines})
}

fn parts_json(p: &HtmlParts) -> Value {
    json!({"novel_text": p.novel_text, "quoted_text": p.quoted_text, "signature_text": p.signature_text})
}

/// Run one vector; `Err(())` when the port signalled an error.
fn call(module: &str, function: &str, a: &Args) -> Option<Result<Value, ()>> {
    let v = match (module, function) {
        ("extraction", "extract_new_text") => Ok(extraction_json(&extract_new_text(
            a.opt("body"),
            a.opt("parent_body"),
            a.opt("html_body"),
        ))),
        ("cleaning", "clean_for_scoring") => Ok(clean_json(&clean_for_scoring(
            a.str("extracted_text"),
            a.opt("html_signature_text"),
        ))),
        ("extraction", "strip_parent_content") => Ok(json!(strip_parent_content(
            a.str("text"),
            a.str("parent_body"),
            a.bool("continuation"),
        ))),
        ("html_text", "html_to_text") => Ok(json!(html_to_text(a.str("html")))),
        ("html_text", "split_html_parts") => Ok(parts_json(&split_html_parts(a.str("html")))),
        ("equivalence", "texts_equivalent") => Ok(json!(texts_equivalent(a.str("a"), a.str("b")))),
        ("equivalence", "tolerant_lines") => Ok(json!(tolerant_lines(a.str("text")))),
        ("cleaning", "is_mobile_tagline") => Ok(json!(internal::is_mobile_tagline(a.str("line")))),
        ("cleaning", "is_signature_delimiter") => {
            Ok(json!(internal::is_signature_delimiter(a.str("line"))))
        }
        ("extraction", "count_unquoted_content_lines") => Ok(json!(
            internal::count_unquoted_content_lines(a.str("body"), a.bool("localized"))
        )),
        ("extraction", "custom_clean") => Ok(json!(internal::custom_clean(
            a.str("text"),
            a.bool("localized")
        ))),
        ("extraction", "find_quote_header_block") => Ok(json!(internal::find_quote_header_block(
            &a.lines("lines"),
            a.bool("localized")
        ))),
        ("extraction", "find_signoff_boundary") => {
            Ok(json!(internal::find_signoff_boundary(&a.lines("lines"))))
        }
        ("extraction", "is_attribution_line") => {
            Ok(json!(internal::is_attribution_line(a.str("line"))))
        }
        ("extraction", "is_quote_line") => Ok(json!(internal::is_quote_line(a.str("line")))),
        ("extraction", "is_signoff_name_line") => {
            Ok(json!(internal::is_signoff_name_line(a.str("text"))))
        }
        ("extraction", "normalize_body") => Ok(json!(internal::normalize_body(a.str("body")))),
        ("extraction", "strip_after_original_message_divider") => Ok(json!(
            internal::strip_after_original_message_divider(&a.lines("lines"), a.bool("localized"))
        )),
        ("extraction", "strip_attribution_lines") => {
            Ok(json!(internal::strip_attribution_lines(&a.lines("lines"))))
        }
        ("fragments", "read_fragments") => match internal::read_fragments(a.str("text")) {
            Ok(frags) => Ok(Value::Array(
                frags
                    .iter()
                    .map(|f| json!({"content": f.content, "quoted": f.quoted}))
                    .collect(),
            )),
            Err(_) => Err(()),
        },
        _ => return None,
    };
    Some(v)
}

/// Run every vector of every file in `dir`; returns (passed, failures, unmapped files).
fn run_vectors(dir: &Path) -> (usize, Vec<String>, Vec<String>) {
    let mut passed = 0;
    let mut failures = Vec::new();
    let mut unmapped = Vec::new();
    for path in vector_files(dir) {
        let file = load(&path);
        let module = file["module"].as_str().expect("module");
        let function = file["function"].as_str().expect("function");
        let params = file["parameters"].as_array().expect("parameters");
        for (n, vector) in file["vectors"]
            .as_array()
            .expect("vectors")
            .iter()
            .enumerate()
        {
            let args = Args {
                args: &vector["args"],
                params,
            };
            let Some(got) = call(module, function, &args) else {
                unmapped.push(format!("{module}.{function}"));
                break;
            };
            let ok = match (vector.get("result"), vector.get("error"), &got) {
                (Some(expected), None, Ok(value)) => expected == value,
                (None, Some(_), Err(())) => true,
                _ => false,
            };
            if ok {
                passed += 1;
            } else {
                failures.push(format!(
                    "{module}.{function} vector {n}\n  args: {}\n  expected: {}\n  got: {:?}",
                    vector["args"],
                    vector
                        .get("result")
                        .or(vector.get("error"))
                        .unwrap_or(&Value::Null),
                    got
                ));
            }
        }
    }
    (passed, failures, unmapped)
}

fn report(kind: &str, passed: usize, failures: &[String], unmapped: &[String]) {
    for f in failures.iter().take(20) {
        eprintln!("{f}");
    }
    eprintln!(
        "{kind}: {passed} passed, {} failed, unmapped: {unmapped:?}",
        failures.len()
    );
}

#[test]
fn public_vectors() {
    let (passed, failures, unmapped) = run_vectors(&conformance_dir().join("vectors"));
    report("public vectors", passed, &failures, &unmapped);
    assert!(
        unmapped.is_empty(),
        "unmapped public functions: {unmapped:?}"
    );
    assert!(
        failures.is_empty(),
        "{} public vectors failed",
        failures.len()
    );
    assert!(passed > 0);
}

#[test]
fn internal_vectors() {
    let (passed, failures, unmapped) = run_vectors(&conformance_dir().join("vectors/internal"));
    report("internal vectors", passed, &failures, &unmapped);
    assert!(
        unmapped.is_empty(),
        "unmapped internal functions: {unmapped:?}"
    );
    assert!(
        failures.is_empty(),
        "{} internal vectors failed",
        failures.len()
    );
    assert!(passed > 0);
}

fn corpus() -> Vec<Value> {
    load(&conformance_dir().join("corpus.json"))["entries"]
        .as_array()
        .expect("entries")
        .clone()
}

#[test]
fn corpus_outputs() {
    let mut failures = Vec::new();
    let entries = corpus();
    for entry in &entries {
        let stem = entry["stem"].as_str().expect("stem");
        let body = entry["body"].as_str();
        let html = entry["html_body"].as_str();
        let mut check = |field: &str, got: Value| {
            if entry[field] != got {
                failures.push(format!(
                    "{stem}: {field}\n  expected: {}\n  got: {got}",
                    entry[field]
                ));
            }
        };
        let stage1 = extract_new_text(body, None, html);
        check("extract_new_text", extraction_json(&stage1));
        let stage2 = clean_for_scoring(&stage1.text, None);
        check("clean_for_scoring", clean_json(&stage2));
        check(
            "html_to_text",
            html.map_or(Value::Null, |h| json!(html_to_text(h))),
        );
        let parts = html.map(split_html_parts);
        check(
            "split_html_parts",
            parts.as_ref().map_or(Value::Null, parts_json),
        );
        let signature = parts
            .as_ref()
            .map(|p| p.signature_text.clone())
            .filter(|s| !s.is_empty());
        check("html_signature_text", json!(signature));
        check(
            "clean_for_scoring_with_html_signature",
            match html {
                None => Value::Null,
                Some(_) => clean_json(&clean_for_scoring(&stage1.text, signature.as_deref())),
            },
        );
        // The reference output matches the hand-labeled ground truth tolerantly.
        let expected = entry["expected"].as_str().expect("expected");
        if !texts_equivalent(&stage2.text, expected) {
            failures.push(format!("{stem}: differs from the hand-labeled expectation"));
        }
    }
    for f in failures.iter().take(20) {
        eprintln!("{f}");
    }
    eprintln!(
        "corpus: {} entries, {} failures",
        entries.len(),
        failures.len()
    );
    assert!(
        failures.is_empty(),
        "{} corpus checks failed",
        failures.len()
    );
}

fn digest(fields: impl Iterator<Item = [String; 4]>) -> String {
    let mut hasher = Sha256::new();
    for entry in fields {
        for s in entry {
            hasher.update(s.len().to_string().as_bytes());
            hasher.update(b":");
            hasher.update(s.as_bytes());
        }
    }
    hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[test]
fn corpus_digest() {
    let recorded = load(&conformance_dir().join("digest.json"));
    assert_eq!(
        recorded["extraction_version"],
        json!(EXTRACTION_VERSION),
        "EXTRACTION_VERSION differs from the recorded generation"
    );
    let expected = recorded["digest"].as_str().expect("digest");
    let entries = corpus();

    let from_recorded = digest(entries.iter().map(|e| {
        [
            e["stem"].as_str().unwrap().to_string(),
            e["clean_for_scoring"]["text"].as_str().unwrap().to_string(),
            e["extract_new_text"]["text"].as_str().unwrap().to_string(),
            e["extract_new_text"]["method"]
                .as_str()
                .unwrap()
                .to_string(),
        ]
    }));
    assert_eq!(
        from_recorded, expected,
        "digest algorithm over the recorded outputs"
    );

    let from_port = digest(entries.iter().map(|e| {
        let stage1 = extract_new_text(e["body"].as_str(), None, e["html_body"].as_str());
        let stage2 = clean_for_scoring(&stage1.text, None);
        [
            e["stem"].as_str().unwrap().to_string(),
            stage2.text,
            stage1.text,
            stage1.method,
        ]
    }));
    assert_eq!(from_port, expected, "digest over this port's outputs");
}

#[test]
fn version_matches_python() {
    let init = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../python/src/email_reply_extractor/__init__.py"),
    )
    .expect("python __init__.py");
    let line = init
        .lines()
        .find(|l| l.starts_with("__version__"))
        .expect("__version__");
    assert!(
        line.contains(&format!("\"{VERSION}\"")),
        "{line} vs {VERSION}"
    );
}
