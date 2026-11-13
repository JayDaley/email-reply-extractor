// Differential check of the TypeScript port against the Python reference.
//
// Usage, from typescript/ after `npm run build`:
//
//   node tools/differential.mjs [cases-per-function] [seed]
//
// Generates inputs by mutating the conformance corpus and vector arguments
// (inserting characters and fragments chosen to exercise the semantic hazards
// in docs/ports.md, deleting ranges, slicing lines, changing case) and by
// generating random HTML, evaluates every call with both implementations, and
// reports each call whose encoded results differ. The Python side runs
// tools/reference.py with ../.venv/bin/python (override with $PYTHON). Not part
// of `npm test`.

import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import * as cleaning from "../dist/cleaning.js";
import * as equivalence from "../dist/equivalence.js";
import * as extraction from "../dist/extraction.js";
import * as fragments from "../dist/fragments.js";
import * as htmlText from "../dist/htmlText.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");
const CONFORMANCE = join(ROOT, "conformance");
const PYTHON = process.env.PYTHON ?? join(ROOT, ".venv", "bin", "python");

const PER_FN = Number(process.argv[2] ?? 400);
let seed = Number(process.argv[3] ?? 1);

// --- seeded PRNG (mulberry32) ----------------------------------------------------------

function rand() {
  seed = (seed + 0x6d2b79f5) | 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const randint = (n) => Math.floor(rand() * n);
const pick = (xs) => xs[randint(xs.length)];

// --- seeds -----------------------------------------------------------------------------

const corpus = JSON.parse(readFileSync(join(CONFORMANCE, "corpus.json"), "utf8")).entries;
const textSeeds = [];
const htmlSeeds = [];
for (const e of corpus) {
  if (e.body) textSeeds.push(e.body);
  if (e.html_body) htmlSeeds.push(e.html_body);
  textSeeds.push(e.expected, e.extract_new_text.text);
}
function collectStrings(value, out) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) out.push(value.join("\n"));
  else if (value && typeof value === "object") for (const v of Object.values(value)) collectStrings(v, out);
}
for (const dir of [join(CONFORMANCE, "vectors"), join(CONFORMANCE, "vectors", "internal")]) {
  for (const f of readdirSync(dir).filter((n) => n.endsWith(".json"))) {
    const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
    for (const v of data.vectors) {
      const out = [];
      collectStrings(v.args, out);
      for (const s of out) (f.includes("html") ? htmlSeeds : textSeeds).push(s);
    }
  }
}

const SPICE = [
  "\r",
  "\r\n",
  "\n",
  "\n\n",
  "\u0085",
  "\u00a0",
  "\u2028",
  "\u2029",
  "\u000b",
  "\u000c",
  "\u001c",
  "\u001f",
  "\ufeff",
  "\u200b",
  "\u200d",
  "\u202f",
  "\u3000",
  "İ",
  "ı",
  "K",
  "ſ",
  "ß",
  "ẞ",
  "é",
  "É",
  "e\u0301",
  "ǅ",
  "²",
  "①",
  "٣",
  "\u{1d49c}",
  "\u{1f600}",
  "Ⅳ",
  "\\",
  "\\U",
  "\\n",
  "\\1",
  "\\2",
  "\\3",
  "\\0",
  "\\12",
  "\\g<1>",
  "\\g<x>",
  "\\g<",
  "\\.",
  "$1",
  "$&",
  "$$",
  ">",
  "> ",
  ">>",
  "...>",
  "  > ",
  "--",
  "-- ",
  "__",
  "-x",
  "|",
  ",",
  ":",
  "：",
  "@",
  "<a@b.example>",
  "wrote:",
  "On ",
  "On Mon, Jan 1, 2026 at 10:00 X wrote:",
  "On Mon,\nX <x@y.z> wrote:",
  "Am 1.1.2026 schrieb X:",
  "Le 1 janv. a écrit :",
  "2026年7月22日 18:12 Xのメール:",
  "From: ",
  "From: A <a@b.c>",
  "De : ",
  "De\u00a0: X <x@y.z>",
  "Objet : ",
  "Envoyé : ",
  "À : ",
  "Sent: ",
  "To: ",
  "Subject: ",
  "Message-ID: <x>",
  "Resent-From: x",
  "-----Original Message-----",
  "-------- Message d'origine --------",
  "--- Mensaje original ---",
  "-----Ursprüngliche Nachricht-----",
  "Cheers,",
  "Best,",
  "Thanks,",
  "Regards",
  "Kind regards,",
  "Mit freundlichen Grüßen",
  "Ngā mihi",
  "Hi all,",
  "Dear Bob,",
  "Sent from my iPhone",
  "Get Outlook for iOS",
  "[Med]",
  "[Med] ACK",
  "[RFC1234]",
  "[1]",
  "Tel: +1 555 0100",
  "www.example.com",
  "https://x.example/z",
  "ORCID: 0000-0001",
  "LinkedIn <https://x.example>",
  "Tokyo Office: 1-2-3",
  "CONFIDENTIALITY NOTICE: x",
  "This email is confidential.",
  "=====",
  "~ Bob",
  "Lucas",
  "Jose\u0301",
  "Thi Nguyen-Huu | CEO",
  "Louis, UCLouvain",
  "VSO BLOG: x",
  "发件人：",
  "主\u3000题：",
  "邮件原件",
  "_______________",
  "wg mailing list -- wg@example.org",
  "To unsubscribe",
  "-----BEGIN PGP",
  "\t",
  " ",
  "  ",
  "x",
  "X",
  "1",
];

const HTML_SPICE = [
  "&amp;",
  "&amp",
  "&AMP;",
  "&noti;",
  "&notit;",
  "&copy2026",
  "&#128;",
  "&#x0;",
  "&#xD800;",
  "&#1114112;",
  "&#65;",
  "&#x41",
  "&#",
  "&#x",
  "&",
  "&;",
  "&nbsp;",
  "&zwnj;",
  "&#13;",
  "&#11;",
  "&#xFFFE;",
  "<",
  "</",
  "</>",
  "< p>",
  "<p>",
  "</p>",
  "<div>",
  "</div>",
  "<br>",
  "<br/>",
  "<p/>",
  "<!--",
  "-->",
  "<!---->",
  "<!-->",
  "--!>",
  "<!x>",
  "<!DOCTYPE html>",
  "<!doctype",
  "<![CDATA[x]]>",
  "<?php x ?>",
  "<blockquote>",
  "</blockquote>",
  '<div class="gmail_quote">',
  '<div class="gmail_quote_container">',
  '<div class="gmail_attr">',
  "<div class=gmail_signature>",
  '<div id="Signature">',
  "<div id=divRplyFwdMsg>",
  '<span class="moz-cite-prefix">',
  '<div class="x" class="gmail_signature">',
  "<script>",
  "</script>",
  "<SCRIPT>",
  "</ScRiPt >",
  "<style>",
  "</style>",
  "<head>",
  "</head>",
  "<title>",
  "</title>",
  "<textarea>",
  "</textarea>",
  "<xmp>",
  "</xmp>",
  "<plaintext>",
  "<iframe>",
  "</iframe>",
  "<table><tr><td>",
  "</td></tr></table>",
  "<li>",
  "<ul>",
  "</ul>",
  "<a href=\"x&amp;y\" title='q'>",
  "<a b='c'd>",
  '<a x=1"y"z w>',
  "<img src=x/>",
  "<b",
  '<p class="&quot;gmail_quote&quot;">',
  "\u00a0",
  "\u001c",
  "\u2028",
  "\r\n",
  "\n",
  " ",
  "x",
  "Bob wrote:",
];

// Positions are aligned to code points so no lone surrogate is produced.
function cpIndex(s, i) {
  if (i > 0 && i < s.length) {
    const c = s.charCodeAt(i);
    if (c >= 0xdc00 && c <= 0xdfff) return i - 1;
  }
  return i;
}

const TWISTS = {
  i: ["\u0130", "\u0131", "I"],
  I: ["\u0130", "\u0131", "i"],
  s: ["\u017f", "S"],
  S: ["\u017f", "s"],
  k: ["\u212a", "K"],
  K: ["\u212a", "k"],
  " ": ["\u00a0", "\t", "\u3000", "\u0085", "\u001c", "  "],
  e: ["\u00e9", "E"],
  u: ["\u00fc", "U"],
};

/** Replace a few characters with their case or whitespace look-alikes. */
function twist(s) {
  const chars = Array.from(s);
  const n = 1 + randint(3);
  for (let k = 0; k < n && chars.length > 0; k++) {
    const i = randint(chars.length);
    const alts = TWISTS[chars[i]];
    if (alts) chars[i] = pick(alts);
  }
  return chars.join("");
}

function mutate(text, spice, ops) {
  let s = text;
  for (let k = 0; k < ops; k++) {
    if (rand() < 0.3) {
      s = twist(s);
      continue;
    }
    const r = rand();
    const i = cpIndex(s, randint(s.length + 1));
    if (r < 0.45) {
      s = s.slice(0, i) + pick(spice) + s.slice(i);
    } else if (r < 0.6 && s.length > 0) {
      const j = cpIndex(s, Math.min(s.length, i + randint(40)));
      s = s.slice(0, i) + s.slice(j);
    } else if (r < 0.75) {
      const lines = s.split("\n");
      const a = randint(lines.length);
      const b = Math.min(lines.length, a + 1 + randint(30));
      s = lines.slice(a, b).join("\n");
    } else if (r < 0.85) {
      const j = cpIndex(s, Math.min(s.length, i + randint(30)));
      const part = s.slice(i, j);
      s = s.slice(0, i) + (rand() < 0.5 ? part.toUpperCase() : part.toLowerCase()) + s.slice(j);
    } else if (r < 0.95) {
      const lines = s.split("\n");
      const a = randint(lines.length);
      lines.splice(a, 0, lines[randint(lines.length)]);
      s = lines.join("\n");
    } else {
      s = s.slice(0, i) + pick(spice) + pick(spice) + pick(spice) + s.slice(i);
    }
  }
  return s;
}

function shortText() {
  const parts = [];
  const n = 1 + randint(6);
  for (let k = 0; k < n; k++) parts.push(pick(SPICE));
  return parts.join(rand() < 0.5 ? " " : "\n");
}

function textInput() {
  const r = rand();
  if (r < 0.2) return shortText();
  const base = pick(textSeeds);
  // Keep big bodies manageable: a random window of lines.
  let s = base;
  if (s.length > 4000 && rand() < 0.8) {
    const lines = s.split("\n");
    const a = randint(lines.length);
    s = lines.slice(a, a + 20 + randint(80)).join("\n");
  }
  return mutate(s, SPICE, randint(6));
}

function htmlSoup() {
  const parts = [];
  const n = 1 + randint(25);
  for (let k = 0; k < n; k++) parts.push(rand() < 0.6 ? pick(HTML_SPICE) : pick(SPICE));
  return parts.join(rand() < 0.3 ? " " : "");
}

function htmlInput() {
  const r = rand();
  if (r < 0.45) return htmlSoup();
  let s = pick(htmlSeeds);
  if (s.length > 6000 && rand() < 0.8) {
    const a = cpIndex(s, randint(s.length));
    s = s.slice(a, cpIndex(s, Math.min(s.length, a + 500 + randint(4000))));
  }
  return mutate(s, rand() < 0.5 ? HTML_SPICE : SPICE, randint(6));
}

/** One line built from pattern vocabulary, twisted. */
function oneLine() {
  const parts = [];
  const n = 1 + randint(4);
  for (let k = 0; k < n; k++) parts.push(pick(SPICE).replaceAll("\n", " "));
  let s = parts.join(pick(["", " ", ", ", "\t"]));
  if (rand() < 0.7) s = twist(s);
  if (rand() < 0.2) s = rand() < 0.5 ? s.toUpperCase() : s.toLowerCase();
  if (rand() < 0.1) s += pick(["\r", "\u0085", "\u2028", " ", "\u000b"]);
  return s;
}

function lines(s) {
  return s.split("\n");
}

// --- the calls -----------------------------------------------------------------------------

const GENERATORS = {
  "extraction.extract_new_text": () => {
    const body = rand() < 0.1 ? null : textInput();
    const args = { body };
    if (rand() < 0.4) args.parent_body = rand() < 0.5 && body ? mutate(body, SPICE, 3) : textInput();
    if (rand() < 0.3) args.html_body = htmlInput();
    return args;
  },
  "cleaning.clean_for_scoring": () => {
    const args = { extracted_text: textInput() };
    if (rand() < 0.3)
      args.html_signature_text = rand() < 0.5 ? shortText() : args.extracted_text.split("\n").slice(-4).join("\n");
    return args;
  },
  "extraction.strip_parent_content": () => {
    const text = textInput();
    return { text, parent_body: rand() < 0.6 ? mutate(text, SPICE, 4) : textInput(), continuation: rand() < 0.5 };
  },
  "html_text.html_to_text": () => ({ html: htmlInput() }),
  "html_text.split_html_parts": () => ({ html: htmlInput() }),
  "equivalence.tolerant_lines": () => ({ text: textInput() }),
  "equivalence.texts_equivalent": () => {
    const a = textInput();
    return {
      a,
      b: rand() < 0.5 ? mutate(a, [" ", "\u00a0", "\n", "\u0085", "\u3000", "\ufeff", "\u001c"], 3) : textInput(),
    };
  },
  "extraction.is_signoff_name_line": () => ({ text: rand() < 0.5 ? shortText() : mutate(pick(SPICE), SPICE, 2) }),
  "extraction.is_attribution_line": () => ({ line: lines(textInput())[0] ?? "" }),
  "extraction.find_quote_header_block": () => ({ lines: lines(textInput()), localized: rand() < 0.7 }),
  "extraction.find_signoff_boundary": () => ({ lines: lines(textInput()) }),
  "extraction.custom_clean": () => ({ text: textInput(), localized: rand() < 0.7 }),
  "extraction.strip_after_original_message_divider": () => ({ lines: lines(textInput()), localized: rand() < 0.7 }),
  "extraction.count_unquoted_content_lines": () => ({ body: textInput(), localized: rand() < 0.7 }),
  "extraction._localized_inline_reply": () => ({
    lines: lines(textInput()),
    parent_body: rand() < 0.5 ? null : textInput(),
  }),
  "extraction._is_author_tag": () => ({
    label: pick(["Med", "SAH", "Linda3", "a.b", "x y", "J\u0301", "İ", "(x", "$", "\\"]),
    head: textInput(),
    parent_body: rand() < 0.5 ? null : textInput(),
  }),
  "_fragments.read_fragments": () => ({ text: extraction.normalizeBody(textInput()) }),
  "cleaning.is_mobile_tagline": () => ({ line: mutate(pick(SPICE), SPICE, 2) }),
  "cleaning._is_droppable_signature_line": () => ({ line: oneLine() }),
  "extraction._is_signoff_name_like": () => ({ text: oneLine() }),
  "extraction.is_quote_line": () => ({ line: oneLine() }),
  "cleaning.strip_greeting_lines": () => ({ lines: [oneLine(), oneLine()] }),
  "cleaning.strip_closing_signoff": () => ({ lines: [oneLine(), oneLine(), oneLine()] }),
  "cleaning.strip_trailing_signoff_debris": () => ({ lines: [oneLine(), oneLine(), oneLine(), oneLine()] }),
  "extraction.strip_attribution_lines": () => ({ lines: [oneLine(), oneLine(), oneLine()] }),
};

const TS = {
  "extraction.extract_new_text": (a) =>
    extraction.extractNewText(a.body, { parentBody: a.parent_body ?? null, htmlBody: a.html_body ?? null }),
  "cleaning.clean_for_scoring": (a) =>
    cleaning.cleanForScoring(a.extracted_text, { htmlSignatureText: a.html_signature_text ?? null }),
  "extraction.strip_parent_content": (a) =>
    extraction.stripParentContent(a.text, a.parent_body, { continuation: a.continuation }),
  "html_text.html_to_text": (a) => htmlText.htmlToText(a.html),
  "html_text.split_html_parts": (a) => htmlText.splitHtmlParts(a.html),
  "equivalence.tolerant_lines": (a) => equivalence.tolerantLines(a.text),
  "equivalence.texts_equivalent": (a) => equivalence.textsEquivalent(a.a, a.b),
  "extraction.is_signoff_name_line": (a) => extraction.isSignoffNameLine(a.text),
  "extraction.is_attribution_line": (a) => extraction.isAttributionLine(a.line),
  "extraction.find_quote_header_block": (a) => extraction.findQuoteHeaderBlock(a.lines, { localized: a.localized }),
  "extraction.find_signoff_boundary": (a) => extraction.findSignoffBoundary(a.lines),
  "extraction.custom_clean": (a) => extraction.customClean(a.text, { localized: a.localized }),
  "extraction.strip_after_original_message_divider": (a) =>
    extraction.stripAfterOriginalMessageDivider(a.lines, { localized: a.localized }),
  "extraction.count_unquoted_content_lines": (a) =>
    extraction.countUnquotedContentLines(a.body, { localized: a.localized }),
  "extraction._localized_inline_reply": (a) => extraction.localizedInlineReply(a.lines, a.parent_body),
  "extraction._is_author_tag": (a) => extraction.isAuthorTag(a.label, a.head, a.parent_body),
  "_fragments.read_fragments": (a) => fragments.readFragments(a.text),
  "cleaning.is_mobile_tagline": (a) => cleaning.isMobileTagline(a.line),
  "cleaning._is_droppable_signature_line": (a) => cleaning.isDroppableSignatureLine(a.line),
  "extraction._is_signoff_name_like": (a) => extraction.isSignoffNameLike(a.text),
  "extraction.is_quote_line": (a) => extraction.isQuoteLine(a.line),
  "cleaning.strip_greeting_lines": (a) => cleaning.stripGreetingLines(a.lines),
  "cleaning.strip_closing_signoff": (a) => cleaning.stripClosingSignoff(a.lines),
  "cleaning.strip_trailing_signoff_debris": (a) => cleaning.stripTrailingSignoffDebris(a.lines),
  "extraction.strip_attribution_lines": (a) => extraction.stripAttributionLines(a.lines),
};

function encode(value) {
  if (Array.isArray(value)) return value.map(encode);
  if (value !== null && typeof value === "object") {
    if ("ignoredLines" in value) return { text: value.text, ignored_lines: [...value.ignoredLines] };
    if ("novelText" in value) {
      return { novel_text: value.novelText, quoted_text: value.quotedText, signature_text: value.signatureText };
    }
    if ("status" in value) return { text: value.text, method: value.method, status: value.status };
    if ("quoted" in value) return { content: value.content, quoted: value.quoted };
  }
  return value;
}

const calls = [];
for (const [fn, gen] of Object.entries(GENERATORS)) {
  for (let k = 0; k < PER_FN; k++) calls.push({ fn, args: gen() });
}

const python = JSON.parse(
  execFileSync(PYTHON, [join(HERE, "reference.py")], {
    input: JSON.stringify(calls),
    maxBuffer: 1 << 30,
    env: { ...process.env, PYTHONPATH: join(ROOT, "python", "src") },
  }).toString("utf8"),
);

const mismatches = [];
const counts = {};
calls.forEach((call, k) => {
  let ts;
  try {
    ts = { result: encode(TS[call.fn](call.args)) };
  } catch (err) {
    ts = { error: err.name };
  }
  const py = python[k];
  const same = "error" in py ? "error" in ts : JSON.stringify(ts.result) === JSON.stringify(py.result);
  counts[call.fn] = counts[call.fn] ?? [0, 0];
  counts[call.fn][0]++;
  if (!same) {
    counts[call.fn][1]++;
    mismatches.push({ ...call, python: py, typescript: ts });
  }
});

for (const [fn, [n, bad]] of Object.entries(counts)) console.log(`${fn}: ${n} cases, ${bad} mismatches`);
const errors = python.filter((r) => "error" in r).length;
const chars = calls.reduce((n, c) => n + JSON.stringify(c.args).length, 0);
console.log(
  `total: ${calls.length} cases (${chars} characters of input, ${errors} raised), ${mismatches.length} mismatches`,
);
if (mismatches.length > 0) {
  const out = process.env.MISMATCHES ?? "differential-mismatches.json";
  writeFileSync(out, JSON.stringify(mismatches, null, 1));
  console.log(`mismatches written to ${out}`);
  process.exitCode = 1;
}
