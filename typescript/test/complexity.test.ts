// Worst-case running time: adversarial inputs complete in linear time.
//
// Mirrors python/tests/test_complexity.py. Each test feeds one input that drove
// a superlinear path of an earlier implementation of this port — regex
// backtracking on V8's engine or a quadratic scan — and asserts that the call
// completes within BOUND_MS. The inputs are sized so that the earlier
// implementation needed more than a minute on most of them and well over the
// bound on the rest (the leading-blank and G7tag cases excepted: their earlier
// cost was high only in the reference, and they are kept for parity); the
// current one needs under two seconds, so the bound is not sensitive to the
// speed of the machine. The larger inputs of the parent-diff cases are built
// before the clock starts (PREPARED_CASES).
//
// The tests pin running time, not output: equivalence of the output with the
// earlier implementation and with the Python reference was established by
// differential testing (tools/differential.mjs) when the paths were rewritten,
// except for the parent-diff aligned-run rule, whose output changed by design
// in generation 7 (docs/generation-7.md).

import assert from "node:assert/strict";
import { test } from "node:test";

import { cleanForScoring } from "../src/cleaning.js";
import {
  customClean,
  extractNewText,
  findQuoteHeaderBlock,
  isAttributionLine,
  localizedInlineReply,
  stripAttributionLines,
  stripParentContent,
} from "../src/extraction.js";
import { readFragments } from "../src/fragments.js";
import { splitHtmlParts } from "../src/htmlText.js";

/** Milliseconds each call may take. */
const BOUND_MS = 5000;

/**
 * A child whose `n` short lines chain backward, one at a time, off three
 * parent-diff seeds, and the parent that proves every link (rule (c)).
 */
function seedChain(n: number): [string, string] {
  const seeds = ["s1 s2 s3 s4 s5 s6 s7 s8", "t1 t2 t3 t4 t5 t6 t7 t8", "u1 u2 u3 u4 u5 u6 u7 u8"];
  const child = [...new Array<string>(n).fill("a"), ...seeds].join("\n");
  const parent = `a a\na ${seeds.join("\n")}`;
  return [child, parent];
}

/**
 * A child and a parent of `n` lines of 30 words each, no word shared: every
 * child line is a rewrap-rule (b) query that is not in the parent.
 */
function distinctLongLines(n: number): [string, string] {
  const lines = (prefix: string): string =>
    Array.from({ length: n }, (_, k) => Array.from({ length: 30 }, (_, j) => `${prefix}${k}x${j}`).join(" ")).join(
      "\n",
    );
  return [lines("c"), lines("p")];
}

/**
 * A re-wrapped paragraph: three 9-word seed lines, then `n` 3-word remainder
 * lines that the continuation rule (c) chains off the seeds, each join a query
 * against a parent of 10-word lines.
 */
function remainderChain(n: number): [string, string] {
  const words = Array.from({ length: 3 * n + 27 }, (_, k) => `w${k}`);
  const parent: string[] = [];
  for (let k = 0; k < words.length; k += 10) parent.push(words.slice(k, k + 10).join(" "));
  const child: string[] = [];
  for (let k = 0; k < 27; k += 9) child.push(words.slice(k, k + 9).join(" "));
  for (let k = 27; k < words.length; k += 3) child.push(words.slice(k, k + 3).join(" "));
  return [child.join("\n"), parent.join("\n")];
}

/**
 * A body with `n` tagged lines below a French quote header, and a parent that
 * uses every tag: each tag is tested against the parent and the text above the
 * block, and none belongs to the author.
 */
function localizedTags(n: number): [string, string] {
  const quoted = "The quoted message carries enough words to be matched in the parent.";
  const tags = Array.from({ length: n }, (_, k) => `[Q${k}] ok\n`).join("");
  const body =
    `Thanks for the review.\n${"zz ".repeat(100000)}\nCheers,\nMed\n` +
    "De : Someone <someone@example.org>\n" +
    "Envoyé : lundi 1 janvier 2026 10:00\n" +
    "À : list@example.org\n" +
    `Objet : Re: review\n${quoted}\n${tags}`;
  const parent = `${"zz ".repeat(100000)}\n${quoted}\n${Array.from({ length: n }, (_, k) => `[Q${k}]`).join(" ")}`;
  return [body, parent];
}

/** A localized quote-header block followed by `n` inline-reply tag lines whose names are not in the head. */
function inlineReplyLines(n: number): string[] {
  const tags = Array.from({ length: n }, (_, i) => `[N${i}] x`);
  return ["hello", "De : a <a@b.c>", "Objet : b", "Envoyé : c", ...tags];
}

// [finding id, call]. Comments give the earlier implementation's cost on V8.
const CASES: [string, () => unknown][] = [
  // MULTI_QUOTE_HDR lookahead (1,600 repeats took 0.12 s, growing quadratically or worse).
  ["A1-readFragments", () => readFragments("On ".repeat(40000))],
  ["A1-extract", () => extractNewText(`hello\n${"On ".repeat(40000)}`)],
  // Attribution collapse as a replacement template: every "\1" expanded to the
  // whole match, so the output was quadratic in size (4,000 repeats built 32 MB).
  ["A8-readFragments", () => readFragments(`On ${"\\1".repeat(60000)} wrote:`)],
  ["A8-extract", () => extractNewText(`hello\nOn ${"\\1".repeat(60000)} wrote:\n> q`)],
  // "<who> <a@b> wrote:": cubic (1,600 repeats took 3.5 s).
  ["A2-isAttributionLine", () => isAttributionLine(`x${"<a@".repeat(6400)}`)],
  ["A2-extract", () => extractNewText(`hello\n${"<a@".repeat(6400)}`)],
  // "Am … schrieb …:" and the wrapped-attribution terminator: quadratic
  // (8,000 repeats took 2.2 s and 0.8 s).
  ["A3-isAttributionLine", () => isAttributionLine(`Am ${"schrieb: ".repeat(80000)}x`)],
  ["A3-wrapped", () => stripAttributionLines(["Am x", `${"schrieb: ".repeat(100000)}x`])],
  ["A3-extract", () => extractNewText(`hello\nAm ${"schrieb: ".repeat(60000)}x`)],
  // "Original message" divider search over a dashed rule: quadratic (20,000
  // dashes took 4.4 s).
  ["A4-extract", () => extractNewText(`hello\n${"-".repeat(120000)}\nbye`)],
  // Sign-off line tail: quadratic (40,000 blanks took 4.6 s).
  ["A5-extract", () => extractNewText(`hello\nbest${" ".repeat(200000)}x`)],
  // Greeting tail, the same shape as A5 (40,000 blanks took 4.9 s).
  ["A5-greeting", () => cleanForScoring(`hi${" ".repeat(200000)}x`)],
  // Titled contact line: cubic, the shape of A2 (1,600 repeats took 3.6 s).
  ["A6-clean", () => cleanForScoring(`hello\nDr x${"<a@".repeat(6400)}`)],
  // Mailing-list footer: quadratic (16,000 repeats took 3.0 s).
  ["A7-clean", () => cleanForScoring(`hello\n${"mailing list ".repeat(100000)}`)],
  // Office / address line (port-specific rewrite of the reference's pattern):
  // quadratic (16,000 blanks took 0.11 s in the pattern alone).
  ["address-line-clean", () => cleanForScoring(`hello\nx Office:${" ".repeat(400000)}`)],
  // Inline-reply tags below a localized quote header: one RegExp with the
  // Unicode \w class was compiled per tag line (port-specific; linear, but about
  // 20 times the reference's cost: 40,000 lines took 3.5 s).
  ["author-tag-localizedInlineReply", () => localizedInlineReply(inlineReplyLines(2000000), null)],
  // A stray close tag scanned the whole open-element stack: quadratic (40,000
  // pairs took 4.3 s).
  ["B1-splitHtmlParts", () => splitHtmlParts("<a>".repeat(200000) + "</b>".repeat(200000))],
  // Every <blockquote> walked the whole stack for open Gmail wrappers (20,000
  // pairs took 4.8 s).
  ["B2-splitHtmlParts", () => splitHtmlParts("<div class=gmail_quote>".repeat(100000) + "<blockquote>".repeat(100000))],
  // Continuation rule: one full pass per backward-chained mark (8,000 lines
  // took 0.9 s).
  ["C4-stripParentContent", () => stripParentContent(...seedChain(80000), { continuation: true })],
  // Quote-header walk from every From: line over one long field run (8,000
  // lines took 11 s).
  ["D1-findQuoteHeaderBlock", () => findQuoteHeaderBlock(new Array<string>(50000).fill("From: a@b x"))],
  ["D1-extract", () => extractNewText(`hello\n${"From: a@b x\n".repeat(20000)}`)],
  // Pasted-evidence walk up a long field run from every From: line (8,000 +
  // 8,000 lines took 18 s).
  ["D2-extract", () => extractNewText(`hello\n\n${"X: y\n".repeat(16000)}${"From: z\n".repeat(16000)}`)],
  // Trailing sign-off debris: the whole tail reclassified per name line (8,000
  // lines took 16 s).
  ["E1-clean", () => cleanForScoring(`hello\n${"A.io\n".repeat(20000)}`)],
  // Custom signature divider: the rest of the text sliced per divider (40,000
  // dividers took 0.6 s).
  ["E2-clean", () => cleanForScoring(`hello\n${"\n===\n".repeat(400000)}`)],
  // Leading blank lines (sliced off at once in this port; popping them one by
  // one from the front was quadratic in the reference).
  ["customClean-leading-blanks", () => customClean(`${"\n".repeat(1000000)}hello`)],
  // Part boundaries: an inline reply of many short answers between quoted
  // lines; the alignment must not rescan the source per line.
  ["parts-extract", () => extractNewText("a\n> q\n".repeat(100000))],
  // Part grouping in stage 2 over many boundaries.
  [
    "parts-clean",
    () => cleanForScoring("a\n".repeat(200000), { partStarts: Array.from({ length: 199999 }, (_, k) => k + 1) }),
  ],
];

// [finding id, input builder, call]. The input is built before timing starts.
// Comments give the generation-6 port's cost on the same input on V8.
const PREPARED_CASES: [string, () => [string, string], (child: string, parent: string) => unknown][] = [
  // Aligned-run rule (a): the port of difflib.SequenceMatcher, quadratic in the
  // number of lines (92 s).
  ["G7a-stripParentContent", () => ["a\n".repeat(40000), "a\n".repeat(40000)], (c, p) => stripParentContent(c, p)],
  // Rewrap rule (b): one substring search of the parent per child line (19 s).
  ["G7b-stripParentContent", () => distinctLongLines(60000), (c, p) => stripParentContent(c, p)],
  // Rule (b) queries that only the suffix automaton decides: every interior
  // word occurs in the parent, which never contains the line (116 s
  // at 1,000,000 lines, linear in the lines: about 46 s here).
  [
    "G7b-automaton-stripParentContent",
    () => ["a a a a a a a b\n".repeat(400000), "a ".repeat(200000)],
    (c, p) => stripParentContent(c, p),
  ],
  // Continuation rule (c): one substring search of the parent per join (184 s).
  ["G7c-stripParentContent", () => remainderChain(300000), (c, p) => stripParentContent(c, p, { continuation: true })],
  // Inline-reply author tags: per tag, a search of the parent and of the text
  // above the quote header (2.2 s in this port, whose name search was already
  // an indexOf scan; 99 s in the reference; kept for parity).
  ["G7tag-extract", () => localizedTags(25000), (body, parent) => extractNewText(body, { parentBody: parent })],
];

for (const [name, call] of CASES) {
  test(`completes in linear time: ${name}`, () => {
    const start = performance.now();
    call();
    const elapsed = performance.now() - start;
    assert.ok(elapsed < BOUND_MS, `took ${(elapsed / 1000).toFixed(2)} s`);
  });
}

for (const [name, build, call] of PREPARED_CASES) {
  test(`prepared input completes in linear time: ${name}`, () => {
    const [child, parent] = build();
    const start = performance.now();
    call(child, parent);
    const elapsed = performance.now() - start;
    assert.ok(elapsed < BOUND_MS, `took ${(elapsed / 1000).toFixed(2)} s`);
  });
}

test("the C4 input marks every line", () => {
  // The C4 input exercises the rule it is meant to: every line chains.
  const [child, parent] = seedChain(50);
  assert.equal(stripParentContent(child, parent, { continuation: true }), "");
});

test("the G7c input marks every line", () => {
  // The G7c input exercises the continuation rule: every remainder chains.
  const [child, parent] = remainderChain(50);
  assert.equal(stripParentContent(child, parent, { continuation: true }), "");
});

test("the G7tag input reaches every tag", () => {
  // The G7tag input keeps the localized treatment: no tag is the author's, so
  // the quote header truncates and only the text above it is left.
  const [body, parent] = localizedTags(50);
  const result = extractNewText(body, { parentBody: parent });
  assert.ok(result.text.endsWith("Cheers,\nMed"));
  assert.ok(!result.text.includes("[Q"));
});
