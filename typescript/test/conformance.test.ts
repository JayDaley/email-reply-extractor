// Replays the language-neutral conformance data in ../conformance (generated
// from the Python reference implementation; format in conformance/README.md):
// every public and internal vector, every recorded corpus output, and the
// corpus digest.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { cleanForScoring, isMobileTagline, isSignatureDelimiter } from "../src/cleaning.js";
import { textsEquivalent, tolerantLines } from "../src/equivalence.js";
import {
  countUnquotedContentLines,
  customClean,
  EXTRACTION_VERSION,
  extractNewText,
  findQuoteHeaderBlock,
  findSignoffBoundary,
  isAttributionLine,
  isQuoteLine,
  isSignoffNameLine,
  normalizeBody,
  stripAfterOriginalMessageDivider,
  stripAttributionLines,
  stripParentContent,
} from "../src/extraction.js";
import { readFragments } from "../src/fragments.js";
import { htmlToText, splitHtmlParts } from "../src/htmlText.js";

/** The repository's conformance/ directory, found by walking up from this file. */
function conformanceDir(): string {
  let dir = dirname(fileURLToPath(import.meta.url));
  for (;;) {
    const candidate = join(dir, "conformance");
    if (existsSync(join(candidate, "digest.json"))) return candidate;
    const parent = dirname(dir);
    if (parent === dir) throw new Error("conformance/ not found above the test file");
    dir = parent;
  }
}

const CONFORMANCE = conformanceDir();

// biome-ignore lint/suspicious/noExplicitAny: JSON data
type Json = any;

function load(path: string): Json {
  const data = JSON.parse(readFileSync(path, "utf8"));
  assert.equal(data.format, 1, `${path}: unknown format ${data.format}`);
  return data;
}

// --- encoding TypeScript results under the Python field names ------------------------

function encode(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(encode);
  if (value !== null && typeof value === "object") {
    const o = value as Record<string, unknown>;
    if ("ignoredLines" in o) return { text: o.text, ignored_lines: [...(o.ignoredLines as number[])] };
    if ("novelText" in o) {
      return { novel_text: o.novelText, quoted_text: o.quotedText, signature_text: o.signatureText };
    }
    if ("status" in o) return { text: o.text, method: o.method, status: o.status };
    if ("quoted" in o) return { content: o.content, quoted: o.quoted };
  }
  return value;
}

// --- function table: Python name -> call with the vector's args ----------------------

type Call = (args: Record<string, Json>) => unknown;

const PUBLIC: Record<string, Call> = {
  extract_new_text: (a) => extractNewText(a.body, { parentBody: a.parent_body ?? null, htmlBody: a.html_body ?? null }),
  clean_for_scoring: (a) => cleanForScoring(a.extracted_text, { htmlSignatureText: a.html_signature_text ?? null }),
  strip_parent_content: (a) => stripParentContent(a.text, a.parent_body, { continuation: a.continuation ?? false }),
  html_to_text: (a) => htmlToText(a.html),
  split_html_parts: (a) => splitHtmlParts(a.html),
  texts_equivalent: (a) => textsEquivalent(a.a, a.b),
  tolerant_lines: (a) => tolerantLines(a.text),
};

const INTERNAL: Record<string, Call> = {
  "cleaning.is_mobile_tagline": (a) => isMobileTagline(a.line),
  "cleaning.is_signature_delimiter": (a) => isSignatureDelimiter(a.line),
  "extraction.count_unquoted_content_lines": (a) =>
    countUnquotedContentLines(a.body, { localized: a.localized ?? true }),
  "extraction.custom_clean": (a) => customClean(a.text, { localized: a.localized ?? true }),
  "extraction.find_quote_header_block": (a) => findQuoteHeaderBlock(a.lines, { localized: a.localized ?? true }),
  "extraction.find_signoff_boundary": (a) => findSignoffBoundary(a.lines),
  "extraction.is_attribution_line": (a) => isAttributionLine(a.line),
  "extraction.is_quote_line": (a) => isQuoteLine(a.line),
  "extraction.is_signoff_name_line": (a) => isSignoffNameLine(a.text),
  "extraction.normalize_body": (a) => normalizeBody(a.body),
  "extraction.strip_after_original_message_divider": (a) =>
    stripAfterOriginalMessageDivider(a.lines, { localized: a.localized ?? true }),
  "extraction.strip_attribution_lines": (a) => stripAttributionLines(a.lines),
  "fragments.read_fragments": (a) => readFragments(a.text),
};

function withDefaults(file: Json, args: Record<string, Json>): Record<string, Json> {
  const full: Record<string, Json> = {};
  for (const p of file.parameters) {
    if (p.name in args) full[p.name] = args[p.name];
    else if ("default" in p) full[p.name] = p.default;
    else throw new Error(`${file.function}: missing required argument ${p.name}`);
  }
  return full;
}

function replay(path: string, fn: Call): void {
  const file = load(path);
  file.vectors.forEach((vector: Json, i: number) => {
    const args = withDefaults(file, vector.args);
    if ("error" in vector) {
      assert.throws(() => fn(args), Error, `${file.function} vector ${i} should raise ${vector.error}`);
    } else {
      assert.deepStrictEqual(encode(fn(args)), vector.result, `${file.function} vector ${i}`);
    }
  });
}

describe("public vectors", () => {
  const dir = join(CONFORMANCE, "vectors");
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
  test("every public function has a vector file and a port", () => {
    assert.deepStrictEqual(files.map((f) => f.replace(/\.json$/, "")).sort(), Object.keys(PUBLIC).sort());
  });
  for (const f of files) {
    test(f, () => {
      const fn = PUBLIC[f.replace(/\.json$/, "")];
      assert.ok(fn, `no port mapped for ${f}`);
      replay(join(dir, f), fn);
    });
  }
});

describe("internal vectors", () => {
  const dir = join(CONFORMANCE, "vectors", "internal");
  for (const f of readdirSync(dir).filter((name) => name.endsWith(".json"))) {
    test(f, () => {
      const fn = INTERNAL[f.replace(/\.json$/, "")];
      assert.ok(fn, `no port mapped for internal vector file ${f}`);
      replay(join(dir, f), fn);
    });
  }
});

// --- corpus ----------------------------------------------------------------------------

const corpus = load(join(CONFORMANCE, "corpus.json"));

describe("corpus", () => {
  for (const entry of corpus.entries) {
    test(entry.stem, () => {
      const stage1 = extractNewText(entry.body, { htmlBody: entry.html_body });
      assert.deepStrictEqual(encode(stage1), entry.extract_new_text, "extract_new_text");
      assert.deepStrictEqual(encode(cleanForScoring(stage1.text)), entry.clean_for_scoring, "clean_for_scoring");
      if (entry.html_body === null) {
        for (const key of ["html_to_text", "split_html_parts", "html_signature_text"]) {
          assert.equal(entry[key], null, key);
        }
        assert.equal(entry.clean_for_scoring_with_html_signature, null);
      } else {
        assert.equal(htmlToText(entry.html_body), entry.html_to_text, "html_to_text");
        const parts = splitHtmlParts(entry.html_body);
        assert.deepStrictEqual(encode(parts), entry.split_html_parts, "split_html_parts");
        const signature = parts.signatureText || null;
        assert.equal(signature, entry.html_signature_text, "html_signature_text");
        assert.deepStrictEqual(
          encode(cleanForScoring(stage1.text, { htmlSignatureText: signature })),
          entry.clean_for_scoring_with_html_signature,
          "clean_for_scoring_with_html_signature",
        );
      }
      assert.deepStrictEqual(
        tolerantLines(cleanForScoring(stage1.text).text),
        tolerantLines(entry.expected),
        "tolerant match with the hand-labeled expected text",
      );
    });
  }
});

// --- digest ----------------------------------------------------------------------------

function digest(fields: string[][]): string {
  const hash = createHash("sha256");
  for (const entry of fields) {
    for (const value of entry) {
      const bytes = Buffer.from(value, "utf8");
      hash.update(`${bytes.length}:`, "latin1");
      hash.update(bytes);
    }
  }
  return hash.digest("hex");
}

describe("digest", () => {
  const recorded = load(join(CONFORMANCE, "digest.json"));

  test("the algorithm matches the worked example in conformance/README.md", () => {
    const expected = createHash("sha256").update("1:a2:Hi6:Hi\nBob3:erp", "utf8").digest("hex");
    assert.equal(digest([["a", "Hi", "Hi\nBob", "erp"]]), expected);
  });

  test("extraction_version equals EXTRACTION_VERSION", () => {
    assert.equal(recorded.extraction_version, EXTRACTION_VERSION);
  });

  test("the algorithm reproduces the recorded digest from the recorded outputs", () => {
    const fields = corpus.entries.map((e: Json) => [
      e.stem,
      e.clean_for_scoring.text,
      e.extract_new_text.text,
      e.extract_new_text.method,
    ]);
    assert.equal(digest(fields), recorded.digest);
  });

  test("this port's outputs reproduce the recorded digest", () => {
    const fields = corpus.entries.map((e: Json) => {
      const stage1 = extractNewText(e.body, { htmlBody: e.html_body });
      return [e.stem, cleanForScoring(stage1.text).text, stage1.text, stage1.method];
    });
    assert.equal(digest(fields), recorded.digest);
  });
});
