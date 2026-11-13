// Tests of the TypeScript API surface: exports, argument conventions, result
// shapes. Extraction behaviour is asserted by the conformance suite.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import * as api from "../src/index.js";
import {
  type CleanResult,
  cleanForScoring,
  EXTRACTION_VERSION,
  type ExtractionResult,
  extractNewText,
  type HtmlParts,
  htmlToText,
  STATUS_EMPTY,
  STATUS_FAILED,
  STATUS_OK,
  splitHtmlParts,
  stripParentContent,
  textsEquivalent,
  tolerantLines,
  VERSION,
} from "../src/index.js";

const BODY = `Hi Dan,

Two comments below.

On Tue, Jul 7, 2026, at 03:25, Dan Wing wrote:
> I would lean heavily towards solution (2).

Agreed, and the draft already says so.

Best,
Lucas
`;

test("the public exports", () => {
  assert.deepStrictEqual(Object.keys(api).sort(), [
    "EXTRACTION_VERSION",
    "STATUS_EMPTY",
    "STATUS_FAILED",
    "STATUS_OK",
    "VERSION",
    "cleanForScoring",
    "extractNewText",
    "htmlToText",
    "splitHtmlParts",
    "stripParentContent",
    "textsEquivalent",
    "tolerantLines",
  ]);
});

test("VERSION equals package.json version", () => {
  const pkg = JSON.parse(readFileSync(new URL("../../package.json", import.meta.url), "utf8"));
  assert.equal(VERSION, pkg.version);
});

test("constants", () => {
  assert.equal(EXTRACTION_VERSION, 6);
  assert.deepStrictEqual([STATUS_OK, STATUS_EMPTY, STATUS_FAILED], ["ok", "empty", "failed"]);
});

test("quickstart example", () => {
  const result: ExtractionResult = extractNewText(BODY);
  assert.equal(result.status, "ok");
  assert.equal(result.method, "erp");
  assert.equal(result.text, "Hi Dan,\n\nTwo comments below.\n\nAgreed, and the draft already says so.\n\nBest,\nLucas");
  const cleaned: CleanResult = cleanForScoring(result.text);
  assert.equal(cleaned.text, "Two comments below.\n\nAgreed, and the draft already says so.");
  assert.deepStrictEqual(cleaned.ignoredLines, [0, 6, 7]);
});

test("results are frozen plain objects", () => {
  const result = extractNewText(BODY);
  assert.ok(Object.isFrozen(result));
  assert.deepStrictEqual(Object.keys(result), ["text", "method", "status"]);
  const cleaned = cleanForScoring(result.text);
  assert.ok(Object.isFrozen(cleaned));
  assert.ok(Object.isFrozen(cleaned.ignoredLines));
  const parts: HtmlParts = splitHtmlParts("<p>a</p>");
  assert.ok(Object.isFrozen(parts));
  assert.deepStrictEqual(Object.keys(parts), ["novelText", "quotedText", "signatureText"]);
});

test("missing and blank bodies", () => {
  const empty = { text: "", method: "none", status: "empty" };
  assert.deepStrictEqual({ ...extractNewText(null) }, empty);
  assert.deepStrictEqual({ ...extractNewText(undefined) }, empty);
  assert.deepStrictEqual({ ...extractNewText(" \n\t") }, empty);
});

test("options objects: omitted, undefined and null are equivalent", () => {
  const html = "<div>Hello there, this is new.</div><blockquote>old text</blockquote>";
  const a = extractNewText(null, { htmlBody: html });
  assert.equal(a.method, "html-erp");
  assert.equal(a.text, "Hello there, this is new.");
  assert.deepStrictEqual(extractNewText(BODY, { parentBody: null, htmlBody: null }), extractNewText(BODY));
  assert.deepStrictEqual(extractNewText(BODY, {}), extractNewText(BODY));
  assert.deepStrictEqual(cleanForScoring("x", { htmlSignatureText: null }), cleanForScoring("x"));
  assert.equal(stripParentContent("a\n\n\nb", "zzz", { continuation: true }), "a\n\nb");
});

test("a template error in the scanner is a failed result, not an exception", () => {
  const body = "My reply.\n\nOn Mon, Jan 1, 2026 at 10:00 C:\\Users\\x wrote:\n> quoted\n";
  assert.deepStrictEqual({ ...extractNewText(body) }, { text: "", method: "failed", status: "failed" });
});

test("HTML helpers and equivalence", () => {
  assert.equal(htmlToText("<p>a&amp;b</p><br>c"), "a&b\n\nc");
  assert.equal(htmlToText(""), "");
  assert.deepStrictEqual(tolerantLines(" a\u00a0\n\n b "), ["a", "b"]);
  assert.equal(textsEquivalent("a\n\nb", " a\nb "), true);
  assert.equal(textsEquivalent("a", "b"), false);
});
