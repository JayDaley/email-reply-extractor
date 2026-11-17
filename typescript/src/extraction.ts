/**
 * New-text extraction: recover the author's novel content (stage 1 of 2).
 *
 * Port of the reference's `extraction.py`. Its module docstring is the design
 * documentation; docs/extraction.md restructures it. The HTML oracle is in
 * {@link splitHtmlParts}.
 *
 * @module
 */

import { absorb, finish, GramTable, HASH_START, StringTable, Transitions } from "./hashTables.js";
import { htmlToText, splitHtmlParts } from "./htmlText.js";
import {
  codePointAt,
  cpLen,
  cpTail,
  firstIsUpper,
  hasPyDigit,
  isBlank,
  isPyAlpha,
  isPyLower,
  isSpaceCode,
  PyPattern,
  pyRstrip,
  pyRstripChars,
  pySplit,
  pyStrip,
  pyWordCount,
  WORD_RE_CLASS,
} from "./pyCompat.js";

// --- generation stamp -----------------------------------------------------------

/**
 * The generation of the text-deriving routine (this module, `cleaning` and
 * `htmlText` together, and their counterparts in the other ports). Consumers stamp stored derived text with it and compare with `<`, so
 * it only ever increases. See docs/versioning.md.
 */
export const EXTRACTION_VERSION = 10;

// --- result -------------------------------------------------------------------

/** Nothing left to score (blank body, or a body that reduced to quotes alone). */
export const STATUS_EMPTY = "empty";
/** Text was extracted. */
export const STATUS_OK = "ok";
/** Extraction raised internally; `text` is empty. */
export const STATUS_FAILED = "failed";

/** One of {@link STATUS_OK}, {@link STATUS_EMPTY}, {@link STATUS_FAILED}. */
export type ExtractionStatus = typeof STATUS_OK | typeof STATUS_EMPTY | typeof STATUS_FAILED;

/**
 * The outcome of {@link extractNewText} on one body (stage 1): the author's
 * full novel content, greetings, sign-offs and signature blocks included.
 */
export interface ExtractionResult {
  /** The author's newly written text, furniture included (may be empty). */
  readonly text: string;
  /**
   * Which path produced the text: `none`, `custom` (the cleanup pass) or
   * `failed`, with an optional `html-` prefix and `+parent-diff` /
   * `+html-quote` suffixes.
   */
  readonly method: string;
  /** One of `ok`, `empty`, `failed`. */
  readonly status: ExtractionStatus;
  /**
   * The 0-based indices into `text.split("\n")` of the lines that begin a new
   * part: a run of the author's text separated from the previous run by
   * content stage 1 removed (a quoted block, an attribution, parent-diff or
   * HTML-quote content). An inline reply written between quoted passages has
   * one part per answer. Empty when the text is a single part, when it is
   * empty, and when the text could not be aligned with its source. The text
   * does not depend on this field; pass it to `cleanForScoring` as
   * `partStarts` to get the cleaned text divided into the same parts.
   */
  readonly partStarts: readonly number[];
}

/** Options of {@link extractNewText}. */
export interface ExtractOptions {
  /** The thread parent's raw body, resolved by the caller from `In-Reply-To`. */
  readonly parentBody?: string | null;
  /** The message's decoded `text/html` part. */
  readonly htmlBody?: string | null;
}

/** The `localized` option of the quote-header and divider helpers. */
export interface LocalizedOptions {
  /** Recognize the Latin-script localized blocks and dividers (default `true`). */
  readonly localized?: boolean;
}

// --- pre-normalization --------------------------------------------------------

/**
 * BOM / zero-width space / zero-width non-joiner / zero-width joiner /
 * word-joiner. U+0000 is removed as well (see {@link GENERIC_ATTRIBUTION_MARK}).
 */
// biome-ignore lint/complexity/useRegexLiterals: escapes keep the invisible characters readable
const ZERO_WIDTH_RE = new RegExp("[\\ufeff\\u200b\\u200c\\u200d\\u2060]", "gu");

/** Normalize line endings and strip BOM / zero-width characters and U+0000. */
export function normalizeBody(body: string): string {
  return body.replaceAll("\r\n", "\n").replaceAll("\r", "\n").replaceAll("\0", "").replace(ZERO_WIDTH_RE, "");
}

// --- line classifiers ---------------------------------------------------------

const QUOTE_RE = new PyPattern(String.raw`^[ \t]*(?:\.{2,}[ \t]*)?>+`);

/** True for a quoted line (any indentation; also the `...>` flowed form). */
export function isQuoteLine(line: string): boolean {
  return QUOTE_RE.test(line);
}

// --- attribution lines --------------------------------------------------------

// Two forms are not run as single regexes, because the engine's backtracking
// over them is polynomial on adversarial lines (a line of "<a@" repeated, or of
// "schrieb:" repeated): "<who> <a@b> wrote:" ({@link isAddressAttribution}) and
// the German "Am <date> schrieb <who>:" ({@link isGermanAttribution}). Each is
// decided by a linear helper that accepts exactly the strings the original
// pattern (given in its comment) accepts under `re.match`.
const ATTRIBUTION_RES: readonly PyPattern[] = [
  // "On <date>, <who> wrote:", also after a "--". No word boundary before
  // "wrote": an unwrapped attribution can glue the address onto it.
  new PyPattern(String.raw`^[ \t]*(?:--)?On\b.*wrote:[ \t]*$`),
  new PyPattern(String.raw`^[ \t]*\d{4}(?:年|[/.-]).*のメール:[ \t]*$`),
];

/**
 * The span a newline-free pattern ending in `$` can cover, or `null`.
 *
 * Without `re.MULTILINE`, `$` matches at the end of the string or before a
 * final `\n`. A pattern none of whose parts can match `\n` (`.`, `[ \t]`,
 * literals) can therefore only reach `$` when the string has no `\n` (the
 * whole string), or a single `\n` as its last character (the string without
 * it). Any other `\n` makes such a pattern fail.
 */
export function dollarLine(text: string): string | null {
  const nl = text.indexOf("\n");
  if (nl < 0) return text;
  if (nl === text.length - 1) return text.slice(0, nl);
  return null;
}

/** The tail of the address attribution form, applied with `match` just after the closing `>`. */
const ADDRESS_ATTRIBUTION_TAIL_RE = new PyPattern(String.raw`[ \t]+wrote:[ \t]*$`);

/**
 * True when `.*<[^>]+@[^>]+>` followed by `tailRe` matches `text` at `start`
 * (`re.match` semantics, `.` excluding `\n`), in linear time.
 *
 * `tailRe` must end in `$` and must not be able to match `>`. Then the `>` of
 * any match is the last `>` in `text`, and the `<` lies after the `>` before it
 * (`[^>]+` cannot cross one), at or after `start`, and before the first `\n` at
 * or after `start` (`.*` cannot cross one). The leftmost such `<` leaves the
 * widest span for `[^>]+@[^>]+`, which needs an `@` with at least one
 * character on each side. In UTF-16 units the `+2` / `-1` bounds hold
 * unchanged: one code unit or more is one code point or more, and every
 * delimiter is a single code unit.
 */
export function endsWithBracketedAddress(text: string, start: number, tailRe: PyPattern): boolean {
  const gt = text.lastIndexOf(">");
  if (gt < start || tailRe.match(text, gt + 1) === null) return false;
  let nl = text.indexOf("\n", start);
  if (nl < 0) nl = text.length;
  // Python: text.rfind(">", 0, gt) (lastIndexOf with a negative position would search index 0).
  const prevGt = gt > 0 ? text.lastIndexOf(">", gt - 1) : -1;
  const lt = text.indexOf("<", Math.max(start, prevGt + 1));
  if (lt < 0 || lt >= Math.min(gt, nl)) return false;
  const at = text.indexOf("@", lt + 2);
  return at >= 0 && at < gt - 1;
}

/**
 * `re.match(r"^.*<[^>]+@[^>]+>[ \t]+wrote:[ \t]*$", line)`, in linear time.
 *
 * "<who> <a@b> wrote:" (no leading "On"); "<who> via Datatracker <a@b>
 * wrote:" and similar are covered too.
 */
function isAddressAttribution(line: string): boolean {
  return endsWithBracketedAddress(line, 0, ADDRESS_ATTRIBUTION_TAIL_RE);
}

const AM_PREFIX_RE = new PyPattern(String.raw`[ \t]*Am\b`);
const SCHRIEB_RE = new PyPattern(String.raw`\bschrieb\b`);

/**
 * `re.match(r"^[ \t]*Am\b.*\bschrieb\b.*:[ \t]*$", line)`, in linear time.
 *
 * German: "Am <date> schrieb <who>:". No part of the pattern matches `\n`, so
 * it applies to {@link dollarLine}'s span as a whole. After the prefix, `.*`
 * can skip anything, so the condition is: a `\bschrieb\b` occurs (it cannot
 * start inside the prefix, which is `[ \t]*Am` followed by a non-word
 * character), and the span, with trailing blanks removed, ends in a `:` at or
 * after the end of the first occurrence.
 */
function isGermanAttribution(line: string): boolean {
  const span = dollarLine(line);
  if (span === null || AM_PREFIX_RE.match(span) === null) return false;
  const schrieb = SCHRIEB_RE.search(line);
  const body = pyRstripChars(span, " \t");
  return schrieb !== null && body.endsWith(":") && body.length - 1 >= schrieb.index + schrieb[0].length;
}

/**
 * A wrapped attribution's start token, optionally after `>` markers and blanks
 * and then a "--".
 */
const ATTRIBUTION_START_RE = new PyPattern(String.raw`^[ \t>]*(?:--)?(?:(?:On|Am|Le|El|El día)\b|\d{4}(?:年|[/.-]))`);
/** A wrapped attribution's terminator lies on one of this many lines below its start line ... */
const WRAPPED_ATTRIBUTION_MAX_LINES = 4;
/** ... and the lines from the start line through the terminator line hold at most this many code points. */
const WRAPPED_ATTRIBUTION_MAX_CHARS = 400;
/**
 * The terminators, except `\bschrieb\b.*:`, which {@link hasAttributionEnd}
 * decides separately: under `search` the engine retried `.*:` from every
 * "schrieb", which is quadratic. The remaining alternatives are literals, or
 * `\s*` runs that follow distinct literals, and search in linear time.
 */
const ATTRIBUTION_END_RE = new PyPattern(String.raw`(?:wrote:|\ba écrit\s*:|\bescribió:|のメール:)[ \t]*$`);

/**
 * True when `line` contains a wrapped attribution's terminator.
 *
 * Equivalent to `re.search` of the original pattern
 * `(?:wrote:|\bschrieb\b.*:|\ba écrit\s*:|\bescribió:|のメール:)[ \t]*$`. Its
 * `\bschrieb\b.*:[ \t]*$` alternative ends at `$` — the end of the string, or
 * before a final `\n` — so its `:` is the last character other than blanks
 * before that point, and its "schrieb" must lie on the same line as the `:`,
 * ending at or before it. The first "schrieb" at or after the start of that
 * line is the best candidate.
 */
function hasAttributionEnd(line: string): boolean {
  if (ATTRIBUTION_END_RE.search(line) !== null) return true;
  const body = pyRstripChars(line.endsWith("\n") ? line.slice(0, -1) : line, " \t");
  if (!body.endsWith(":")) return false;
  const colon = body.length - 1;
  // Python: body.rfind("\n", 0, colon) + 1.
  const lineStart = colon > 0 ? body.lastIndexOf("\n", colon - 1) + 1 : 0;
  const schrieb = SCHRIEB_RE.search(line, lineStart);
  return schrieb !== null && schrieb.index + schrieb[0].length <= colon;
}

/**
 * True if `line` on its own is a complete attribution line, or a line of a
 * generic attribution unit marked by {@link markGenericAttributions}.
 */
export function isAttributionLine(line: string): boolean {
  return (
    line.startsWith(GENERIC_ATTRIBUTION_MARK) ||
    ATTRIBUTION_RES.some((pat) => pat.test(line)) ||
    isAddressAttribution(line) ||
    isGermanAttribution(line)
  );
}

/**
 * If `lines[i]` opens a wrapped attribution, return the index of its
 * terminator line, else `null`.
 *
 * A start line ({@link ATTRIBUTION_START_RE}) without a terminator of its own,
 * followed by the first line `j` within {@link WRAPPED_ATTRIBUTION_MAX_LINES}
 * lines that holds a terminator, where no line from `i + 1` through `j` is
 * blank or is itself a complete attribution line matching the start pattern,
 * and the lines `i` through `j` hold at most
 * {@link WRAPPED_ATTRIBUTION_MAX_CHARS} code points in total.
 */
export function wrappedAttributionEnd(lines: readonly string[], i: number): number | null {
  const line = lines[i] as string;
  if (!ATTRIBUTION_START_RE.test(line) || hasAttributionEnd(line)) return null;
  let total = cpLen(line);
  for (let j = i + 1; j < Math.min(i + 1 + WRAPPED_ATTRIBUTION_MAX_LINES, lines.length); j++) {
    const next = lines[j] as string;
    if (isBlank(next)) return null;
    total += cpLen(next);
    if (total > WRAPPED_ATTRIBUTION_MAX_CHARS) return null;
    if (ATTRIBUTION_START_RE.test(next) && isAttributionLine(next)) return null;
    if (hasAttributionEnd(next)) return j;
  }
  return null;
}

/** Drop attribution lines, wrapped forms and marked generic attribution lines included. */
export function stripAttributionLines(lines: readonly string[]): string[] {
  const out: string[] = [];
  let i = 0;
  const n = lines.length;
  while (i < n) {
    if (isAttributionLine(lines[i] as string)) {
      i++;
      continue;
    }
    const end = wrappedAttributionEnd(lines, i);
    if (end !== null) {
      i = end + 1;
      continue;
    }
    out.push(lines[i] as string);
    i++;
  }
  return out;
}

// --- generic attribution lines ------------------------------------------------
//
// A generic attribution unit is one to three lines, the last ending in a colon,
// whose text names its source by a cue (an e-mail address, or a clock time and
// a date) or ends in a speech verb. A unit directly above a quote line, with at
// most one blank line between, is removed with the attribution lines; a unit
// with no quote line below can end the new text at a sign-off boundary. The
// rules are stated in the reference's comment above
// `_generic_attribution_start`. Lengths are code points, word counts are
// `len(str.split())`.

/** The longest generic attribution unit, and its last line, in code points. */
const GENERIC_ATTRIBUTION_MAX_CHARS = 400;
/** The most words a head with a cue may hold. */
const CUE_ATTRIBUTION_MAX_WORDS = 25;
/** The most words a head ending in a speech verb may hold. */
const VERB_ATTRIBUTION_MAX_WORDS = 8;
/** The character before the "@" of an e-mail address, and the domain after it. */
const EMAIL_LOCAL_CHAR_RE = new PyPattern(String.raw`[\w.+=-]`);
const EMAIL_DOMAIN_RE = new PyPattern(String.raw`[\w-]+(?:\.[\w-]+)+`);
/** A clock time: "9:30", "19:30", "19h30". */
const CLOCK_TIME_RE = new PyPattern(String.raw`(?<!\d)\d{1,2}(?::\d{2}|h\d{2}\b)`);
/** A date cue: a year from 1900 to 2099, a day and month, a CJK month, or a day and month with dots. */
const DATE_CUE_RE = new PyPattern(
  String.raw`(?<!\d)(?:19|20)\d{2}(?!\d)|(?<![\d.])\d{1,2}[./-]\d{1,2}(?!\d)|\d{1,2}月|\d{1,2}\.\s?\d{1,2}\.`,
);
/** The opening of an attribution whose rest wraps onto the next line. */
const ATTRIBUTION_OPENER_RE = new PyPattern(
  String.raw`^[ \t]*(?:-*On|Le|Am|El|Il|Op|Den|Ar|At|In|Em|Dne|W dniu|Pe|Às|\d)\b`,
);
/** A head that opens with a document reference, optionally after "(". */
const DOCUMENT_REFERENCE_RE = new PyPattern(
  String.raw`^[ \t]*(?:\(?(?:sections?|sec\.|§|figures?|fig\.|tables?|appendix|rfc|old|new|current` +
    String.raw`|proposed)\b)`,
  { ignoreCase: true },
);
/** A head that ends in a URL or in a phrase announcing text below. */
const LEAD_IN_TAIL_RE = new PyPattern(String.raw`(?:https?://|www\.)\S*$|\b(?:as follows|following|below)$`, {
  ignoreCase: true,
});
/** A speech verb at the end of a head; applied to the last {@link SPEECH_VERB_SPAN} code points. */
const SPEECH_VERB_RE = new PyPattern(
  String.raw`(?:\b(?:wrote|writes|said|schrieb|schreibt|[ée]crit|escribi[óo]|escribe|scritto|scrisse` +
    String.raw`|scrive|schreef|schrijft|skrev|skriver|kirjoitti|napisa[łl]a?|escreveu|ysgrifennodd` +
    String.raw`|написал|написала|пишет)|写道|寫道|쓴[ \t]*글)$`,
  { ignoreCase: true },
);
const SPEECH_VERB_SPAN = 24;
/** A head that introduces quoted content rather than a person. */
const CONTENT_LEAD_IN_RE = new PyPattern(
  String.raw`\b(?:it|this|that|what|text|draft|document|spec|section|rfc|paragraph|figure)\b` +
    String.raw`|^[ \t]*[*•-]`,
  { ignoreCase: true },
);

/**
 * Prefixed to every line of a generic attribution unit found above a quote
 * line (see {@link markGenericAttributions}). {@link normalizeBody} removes
 * U+0000 from every body, so no other line carries it.
 */
const GENERIC_ATTRIBUTION_MARK = "\0";

/** `line.rstrip().endswith((":", "："))`. */
function endsWithColon(line: string): boolean {
  const tail = pyRstrip(line);
  return tail.endsWith(":") || tail.endsWith("：");
}

/**
 * `re.search(r"[\w.+=-]+@[\w-]+(?:\.[\w-]+)+", text)`, in linear time: some
 * "@" has a local-part character directly before it and the domain pattern
 * directly after it. The character before the "@" is read as a code point (a
 * surrogate pair is matched from its first unit).
 */
function hasEmailAddress(text: string): boolean {
  let at = text.indexOf("@");
  while (at >= 0) {
    if (at > 0) {
      let prev = at - 1;
      const c = text.charCodeAt(prev);
      if (prev > 0 && c >= 0xdc00 && c <= 0xdfff) {
        const h = text.charCodeAt(prev - 1);
        if (h >= 0xd800 && h <= 0xdbff) prev--;
      }
      if (EMAIL_LOCAL_CHAR_RE.match(text, prev) !== null && EMAIL_DOMAIN_RE.match(text, at + 1) !== null) return true;
    }
    at = text.indexOf("@", at + 1);
  }
  return false;
}

/** The first code point of `line.lstrip()`, or -1 for a blank line. */
function firstNonSpaceCodePoint(line: string): number {
  for (let i = 0; i < line.length; i++) {
    if (!isSpaceCode(line.charCodeAt(i))) return codePointAt(line, i);
  }
  return -1;
}

/**
 * True when `line` reads as the wrapped continuation of `above`: its first
 * non-blank character is lowercase, is not a letter, or lies in U+3000 to
 * U+D7AF; or `above`, without trailing whitespace, does not end in ".", "!",
 * "?" or ":" and opens like an attribution ({@link ATTRIBUTION_OPENER_RE}).
 */
function continues(above: string, line: string): boolean {
  const first = firstNonSpaceCodePoint(line);
  if (first >= 0 && (isPyLower(first) || !isPyAlpha(first) || (first >= 0x3000 && first <= 0xd7af))) return true;
  const tail = pyRstrip(above);
  return tail.length > 0 && !".!?:".includes(tail.charAt(tail.length - 1)) && ATTRIBUTION_OPENER_RE.test(above);
}

/** The first line of the generic attribution unit whose last line is `lines[e]`: up to two lines above it join. */
function unitStart(lines: readonly string[], e: number): number {
  let s = e;
  while (s > e - 2 && s > 0) {
    const above = lines[s - 1] as string;
    if (isBlank(above) || isQuoteLine(above) || !continues(above, lines[s] as string)) break;
    s--;
  }
  return s;
}

/** Step 5 of the generic attribution rules, for a unit's `text` ending in a colon. */
function isGenericAttributionText(text: string): boolean {
  if (cpLen(text) > GENERIC_ATTRIBUTION_MAX_CHARS) return false;
  const head = pyRstripChars(text.slice(0, -1), " \t\u00a0");
  if (!head || DOCUMENT_REFERENCE_RE.test(head) || LEAD_IN_TAIL_RE.search(head) !== null) return false;
  const words = pyWordCount(head);
  if (
    words <= CUE_ATTRIBUTION_MAX_WORDS &&
    (hasEmailAddress(head) || (CLOCK_TIME_RE.search(head) !== null && DATE_CUE_RE.search(head) !== null))
  ) {
    return true;
  }
  return (
    words <= VERB_ATTRIBUTION_MAX_WORDS &&
    SPEECH_VERB_RE.search(cpTail(head, SPEECH_VERB_SPAN)) !== null &&
    CONTENT_LEAD_IN_RE.search(head) === null
  );
}

/** The first line of the generic attribution unit whose last line is `lines[e]`, or `null`. */
function genericAttributionStart(lines: readonly string[], e: number): number | null {
  const line = lines[e] as string;
  if (!endsWithColon(line) || isQuoteLine(line) || cpLen(line) > GENERIC_ATTRIBUTION_MAX_CHARS) return null;
  for (let k = Math.max(0, e - WRAPPED_ATTRIBUTION_MAX_LINES); k < e; k++) {
    if (wrappedAttributionEnd(lines, k) === e) return null;
  }
  if (ATTRIBUTION_START_RE.test(line) && isAttributionLine(line)) return null;
  const s = unitStart(lines, e);
  if (s === e && isAttributionLine(line)) return null;
  const unitText = lines
    .slice(s, e + 1)
    .map(pyStrip)
    .join(" ");
  if (!isGenericAttributionText(unitText)) return null;
  return s;
}

// --- wrap remainders ----------------------------------------------------------
//
// The tail of a long quote line that a client re-wrapped without marking: an
// unmarked line of at most 2 words that starts lowercase, between two quote
// lines, below a quote line of at least 60 code points with no closing
// punctuation (see the reference's comment above `_is_wrap_remainder`).

const WRAP_REMAINDER_MIN_ABOVE = 60;
const WRAP_REMAINDER_MAX_WORDS = 2;
/** An answer word or a URL at the start of a line. */
const ANSWER_WORD_RE = new PyPattern(
  String.raw`^(?:ok(?:ay)?|yes|yep|no|nope|agreed?|done|sure|fine|ack|right|true|correct|see|thanks?` +
    String.raw`|https?://|www\.)\b`,
  { ignoreCase: true },
);

/** True when `line`, between `above` and `below`, is a wrap remainder. */
function isWrapRemainder(above: string, line: string, below: string): boolean {
  const text = pyStrip(line);
  if (!text || isQuoteLine(line) || !isQuoteLine(above) || !isQuoteLine(below)) return false;
  const tail = pyRstrip(above);
  if (cpLen(tail) < WRAP_REMAINDER_MIN_ABOVE || pyWordCount(text) > WRAP_REMAINDER_MAX_WORDS) return false;
  if (!isPyLower(codePointAt(text, 0)) || ANSWER_WORD_RE.test(text)) return false;
  return !'.?!:;)"'.includes(tail.charAt(tail.length - 1));
}

/**
 * Mark the generic attribution units and wrap remainders of `text`: every line
 * of a unit whose last line is followed by a quote line (directly or after one
 * blank line) is prefixed with {@link GENERIC_ATTRIBUTION_MARK}; a wrap
 * remainder is prefixed with `"> "` instead. Each is decided on the unmarked
 * lines. The line count is unchanged.
 */
function markGenericAttributions(text: string): string {
  const lines = text.split("\n");
  const n = lines.length;
  const prefix: string[] = new Array<string>(n).fill("");
  for (let e = 0; e < n; e++) {
    let j = e + 1;
    if (j < n && isBlank(lines[j] as string)) j++;
    if (j >= n || !isQuoteLine(lines[j] as string)) continue;
    const start = genericAttributionStart(lines, e);
    if (start !== null) {
      for (let k = start; k <= e; k++) prefix[k] = GENERIC_ATTRIBUTION_MARK;
    }
  }
  for (let i = 1; i < n - 1; i++) {
    if (isWrapRemainder(lines[i - 1] as string, lines[i] as string, lines[i + 1] as string)) prefix[i] = "> ";
  }
  return lines.map((line, i) => (prefix[i] as string) + line).join("\n");
}

/**
 * The last line of an attribution that starts at `lines[k]`, or `null`: a run
 * of marked generic attribution lines, a complete attribution line, a wrapped
 * attribution, or a generic attribution unit that starts at `k` (whose last
 * line is the first line ending in a colon among `lines[k]` and the two lines
 * below it, with no blank or quote line before it).
 */
function attributionEndAt(lines: readonly string[], k: number): number | null {
  if ((lines[k] as string).startsWith(GENERIC_ATTRIBUTION_MARK)) {
    let e = k;
    while (e + 1 < lines.length && (lines[e + 1] as string).startsWith(GENERIC_ATTRIBUTION_MARK)) e++;
    return e;
  }
  if (isAttributionLine(lines[k] as string)) return k;
  const end = wrappedAttributionEnd(lines, k);
  if (end !== null) return end;
  for (let e = k; e < Math.min(k + 3, lines.length); e++) {
    const line = lines[e] as string;
    if (isBlank(line) || isQuoteLine(line)) return null;
    if (endsWithColon(line)) return genericAttributionStart(lines, e) === k ? e : null;
  }
  return null;
}

// --- sign-off boundary ----------------------------------------------------------

/** Sign-off salutations, ordered longest-first (shared with `cleaning`). */
export const SIGNOFF_SALUTATIONS =
  // English
  String.raw`all\s+the\s+best|best\s+(?:regards|wishes)|kind\s+regards|warm(?:est)?\s+regards|` +
  String.raw`many\s+thanks|thanks\s+again|thank\s+you|yours(?:\s+(?:sincerely|truly|faithfully))?|` +
  String.raw`best|cheers|thanks|thx|regards|sincerely|respectfully|cordially|br|rgds` +
  // German
  String.raw`|mit\s+freundlichen\s+gr(?:ü|ue)(?:ß|ss)en|freundliche\s+gr(?:ü|ue)(?:ß|ss)e|` +
  String.raw`(?:viele|beste|liebe)\s+gr(?:ü|ue)(?:ß|ss)e|gr(?:ü|ue)(?:ß|ss)e?|mfg` +
  // French
  String.raw`|(?:bien\s+)?cordialement|amicalement|amiti(?:é|e)s|salutations` +
  // Spanish
  String.raw`|saludos(?:\s+cordiales)?|un\s+saludo|atentamente` +
  // Dutch
  String.raw`|met\s+vriendelijke\s+groet(?:en)?|groet(?:en|jes)?|mvg` +
  // Māori (common in NZ English mail)
  String.raw`|ng(?:ā|a)\s+mihi(?:\s+nui)?|noho\s+ora\s+mai` +
  // Nordic / other
  String.raw`|v(?:ä|a)nliga\s+h(?:ä|a)lsningar|med\s+v(?:e|ä)nlig\s+h(?:i|ä)lsen|mvh|terveisin` +
  String.raw`|cumprimentos|atenciosamente|ciao|saluti|cordiali\s+saluti`;

/**
 * A line that is only a sign-off salutation (optional trailing `,` `.` or `!`).
 *
 * The tail is `[ \t]*(?:[,.!][ \t]*)?$`, which accepts the same strings as the
 * original `[ \t]*[,.!]?[ \t]*$`: written that way, two adjacent `[ \t]*` runs
 * split a long blank run in quadratically many ways before a non-blank
 * character fails the line.
 */
export const SIGNOFF_LINE_RE = new PyPattern(String.raw`^[ \t]*(?:${SIGNOFF_SALUTATIONS})[ \t]*(?:[,.!][ \t]*)?$`, {
  ignoreCase: true,
});

/** The one-line form: "Cheers, Peter". */
const SIGNOFF_INLINE_RE = new PyPattern(String.raw`^[ \t]*(?:${SIGNOFF_SALUTATIONS})[ \t]*,[ \t]*(?P<name>\S.*)$`, {
  ignoreCase: true,
});

/** One word of a personal name. */
const NAME_TOKEN_RE = new PyPattern(String.raw`^[^\W\d_][\w'’.\-]*$`);

/**
 * True if `text` is plausibly just a person's name (1–4 capitalized-ish words,
 * no digits, short). The first token must start uppercase.
 */
export function isSignoffNameLine(text: string): boolean {
  const s = pyStrip(text);
  if (!s || cpLen(s) > 40 || hasPyDigit(s)) return false;
  const tokens = pySplit(s);
  if (tokens.length < 1 || tokens.length > 4) return false;
  if (!firstIsUpper(tokens[0] as string)) return false;
  return tokens.every((tok) => NAME_TOKEN_RE.test(tok));
}

/** True for a sign-off name line carrying a title/affiliation tail. */
export function isSignoffNameTitleLine(text: string): boolean {
  // re.split(r"[|,]", text.strip(), maxsplit=1)
  const stripped = pyStrip(text);
  const m = /[|,]/.exec(stripped);
  if (m === null) return false;
  const name = pyStrip(stripped.slice(0, m.index));
  const title = pyStrip(stripped.slice(m.index + 1));
  if (!title || cpLen(title) > 40 || pyWordCount(title) > 4) return false;
  return isSignoffNameLine(name);
}

/** A plausible sign-off name line: bare name, or name + title/affiliation. */
export function isSignoffNameLike(text: string): boolean {
  return isSignoffNameLine(text) || isSignoffNameTitleLine(text);
}

/** If a sign-off opens at `lines[i]`, return the index of its name line. */
export function signoffNameIndex(lines: readonly string[], i: number): number | null {
  const line = lines[i] as string;
  if (SIGNOFF_LINE_RE.test(line)) {
    let j = i + 1;
    const n = lines.length;
    while (j < n && isBlank(lines[j] as string)) j++;
    if (j < n && isSignoffNameLike(lines[j] as string)) return j;
    return null;
  }
  const m = SIGNOFF_INLINE_RE.match(line);
  if (m && isSignoffNameLike(m.groups?.name as string)) return i;
  return null;
}

/**
 * Return the index just past a sign-off (salutation + name) that is directly
 * followed by an attribution introducing unmarked text, or `null`.
 *
 * The attribution is the next non-blank line after the name, recognized by
 * {@link attributionEndAt}; it must not be followed, after any blank lines, by
 * a quote line (it would head an interleaved quote).
 */
export function findSignoffBoundary(lines: readonly string[]): number | null {
  const n = lines.length;
  for (let i = 0; i < n; i++) {
    const nameIdx = signoffNameIndex(lines, i);
    if (nameIdx === null) continue;
    let k = nameIdx + 1;
    while (k < n && isBlank(lines[k] as string)) k++;
    if (k >= n) continue;
    const end = attributionEndAt(lines, k);
    if (end === null) continue;
    let q = end + 1;
    while (q < n && isBlank(lines[q] as string)) q++;
    if (q < n && isQuoteLine(lines[q] as string)) continue;
    return nameIdx + 1;
  }
  return null;
}

/** Truncate everything after a sign-off that an attribution line follows. */
export function stripAfterSignoffBoundary(lines: readonly string[]): string[] {
  const idx = findSignoffBoundary(lines);
  return idx === null ? [...lines] : lines.slice(0, idx);
}

// --- forwarded / top-post quote-header blocks ---------------------------------

/** Whitespace accepted around a localized label's colon: space, tab, U+00A0, U+202F. */
const LABEL_SPACE = String.raw`[ \t\u00a0\u202f]`;

/** The localized opener labels ("From" translated). */
const LOCALIZED_FROM_LABELS = "De|Da|Van|Von";

const FROM_LINE_RE = new PyPattern(String.raw`^[ \t]*(?:From:|发件人[ \t]*[:：])[ \t]*\S`);

const LOCALIZED_FROM_LINE_RE = new PyPattern(
  String.raw`^[ \t]*(?:${LOCALIZED_FROM_LABELS})${LABEL_SPACE}*:${LABEL_SPACE}*\S`,
);

const LOCALIZED_FIELD_LABELS =
  String.raw`Envoy[ée]|Date|[ÀA]|Cci?|Objet` +
  String.raw`|Enviad[oa](?:[ \t]+(?:el|em))?|Fecha|Para|CCO|Asunto|Assunto` +
  String.raw`|Data|Inviato|Oggetto` +
  String.raw`|Verzonden|Datum|Aan|Onderwerp` +
  String.raw`|Gesendet|An|Betreff`;

const LOCALIZED_FIELD_RE = new PyPattern(
  String.raw`^[ \t]*(?i:${LOCALIZED_FIELD_LABELS}|${LOCALIZED_FROM_LABELS})` +
    String.raw`${LABEL_SPACE}*:(?:${LABEL_SPACE}|$)`,
);

const LOCALIZED_SUBJECT_RE = new PyPattern(
  String.raw`^[ \t]*(?i:Objet|Asunto|Assunto|Oggetto|Onderwerp|Betreff)` +
    String.raw`${LABEL_SPACE}*:(?:${LABEL_SPACE}|$)`,
);

const CJK_HEADER_LABEL = String.raw`[\u4e00-\u9fff][\u4e00-\u9fff\u3000]{0,10}[ \t]*[:：]`;

const HEADER_FIELD_RE = new PyPattern(String.raw`^[ \t]*(?:[A-Za-z][A-Za-z-]{0,40}:(?:[ \t]|$)|${CJK_HEADER_LABEL})`);

const QUOTE_HEADER_SIGNAL_RE = new PyPattern(
  String.raw`^[ \t]*(?:(?:Sent|Date|To|Cc|Bcc|Reply-To|Subject` +
    String.raw`|Gesendet|An|Betreff)[ \t]*:(?:[ \t]|$)` +
    String.raw`|(?:发送时间|收件人|抄送人?|日期|主\u3000?题)[ \t]*[:：])`,
  { ignoreCase: true },
);

const TRANSPORT_HEADER_RE = new PyPattern(
  String.raw`^[ \t]*(?:Message-ID|References|In-Reply-To|Received|Return-Path` + String.raw`|Resent-[A-Za-z-]+)[ \t]*:`,
  { ignoreCase: true },
);

/**
 * For every index `i`, whether the contiguous header-field run directly above
 * `lines[i]` contains a transport field: a `From:` at `i` then sits inside
 * pasted header evidence rather than opening a client's quote header.
 *
 * Computed in one forward pass — the run above `i` is `lines[i - 1]` plus,
 * when that line is a header field, the run above `i - 1` — instead of walking
 * the run upward from every `From:`, which is quadratic when many `From:`
 * lines follow a long run of fields.
 */
function pastedHeaderRuns(lines: readonly string[]): boolean[] {
  const pasted: boolean[] = new Array<boolean>(lines.length).fill(false);
  for (let i = 1; i < lines.length; i++) {
    const above = lines[i - 1] as string;
    pasted[i] = HEADER_FIELD_RE.test(above) && (TRANSPORT_HEADER_RE.test(above) || (pasted[i - 1] as boolean));
  }
  return pasted;
}

/** One entry of {@link headerWalkTable}: `[fields, hasSignal]`. */
type WalkEntry = readonly [fields: number, hasSignal: boolean];

const WALK_STOP: WalkEntry = [0, false];

/**
 * The field walk of {@link findQuoteHeaderBlock}, for every start.
 *
 * The walk from an opener at `i` visits `lines[i + 1:]` and stops at the first
 * line that is neither a header field, nor a folded continuation, nor
 * (`tolerant` only) a single blank directly above a field. Whether it stops at
 * a line depends only on that line, the next one and the mode (`isLocalized`,
 * `tolerant`), never on where the walk started. Entry `j`
 * (`0 <= j <= lines.length`) holds `[fields, hasSignal]` for a walk entering
 * at `j`: the number of field lines it passes and whether one of them is a
 * signal field. Built backward in one pass, so each opener is decided in
 * constant time; rewalking from every opener is quadratic when many openers
 * share one long run.
 */
function headerWalkTable(lines: readonly string[], isLocalized: boolean, tolerant: boolean): WalkEntry[] {
  const isField = (text: string): boolean =>
    HEADER_FIELD_RE.test(text) || (isLocalized && LOCALIZED_FIELD_RE.test(text));
  const signalRe = isLocalized ? LOCALIZED_SUBJECT_RE : QUOTE_HEADER_SIGNAL_RE;
  const n = lines.length;
  const field = lines.map(isField);
  const table: WalkEntry[] = new Array<WalkEntry>(n + 1).fill(WALK_STOP);
  for (let j = n - 1; j >= 0; j--) {
    const nxt = lines[j] as string;
    const [fields, hasSignal] = table[j + 1] as WalkEntry;
    if (field[j]) {
      table[j] = [fields + 1, hasSignal || signalRe.test(nxt)];
    } else if (!isBlank(nxt) && (tolerant || nxt.charAt(0) === " " || nxt.charAt(0) === "\t")) {
      // Folded continuation of the preceding header field.
      table[j] = [fields, hasSignal];
    } else if (isBlank(nxt) && tolerant && j + 1 < n && field[j + 1]) {
      // Double-spaced rendering: a single blank between fields.
      table[j] = [fields, hasSignal];
    }
    // Otherwise the walk stops at j: the entry stays [0, false].
  }
  return table;
}

/**
 * Return the index of the `From:` line that opens a forwarded/quote-header
 * block, or `null` if the body has no such block. `localized: false`
 * recognizes only the English, German-label and Chinese blocks.
 *
 * The pasted-evidence flags ({@link pastedHeaderRuns}) and each walk mode's
 * table ({@link headerWalkTable}) are built once, on first use, so the search
 * is linear in the number of lines.
 */
export function findQuoteHeaderBlock(lines: readonly string[], options: LocalizedOptions = {}): number | null {
  const localized = options.localized ?? true;
  const n = lines.length;
  let pasted: boolean[] | null = null;
  const tables = new Map<string, WalkEntry[]>();
  for (let i = 0; i < n; i++) {
    const line = lines[i] as string;
    let isLocalized: boolean;
    if (FROM_LINE_RE.test(line)) {
      isLocalized = false;
    } else if (localized && LOCALIZED_FROM_LINE_RE.test(line)) {
      isLocalized = true;
    } else {
      continue;
    }
    if (pasted === null) pasted = pastedHeaderRuns(lines);
    if (pasted[i]) continue;
    const tolerant = line.includes("@");
    const mode = `${isLocalized}/${tolerant}`;
    let table = tables.get(mode);
    if (table === undefined) {
      table = headerWalkTable(lines, isLocalized, tolerant);
      tables.set(mode, table);
    }
    const [fields, hasSignal] = table[i + 1] as WalkEntry;
    if (hasSignal && 1 + fields >= (isLocalized ? 3 : 2)) return i;
  }
  return null;
}

/** The separator Alibaba Mail draws directly above its quote-header block. */
const QUOTE_HEADER_DIVIDER_RE = new PyPattern(String.raw`^[ \t]*-{6,}[ \t]*$`);

/**
 * Truncate everything from a forwarded/quote-header block to the end. A dashed
 * divider line directly above the block, or failing that a bare "Original"
 * line, is dropped with it.
 */
export function stripAfterQuoteHeaderBlock(lines: readonly string[], options: LocalizedOptions = {}): string[] {
  const idx = findQuoteHeaderBlock(lines, options);
  if (idx === null) return [...lines];
  const out = lines.slice(0, idx);
  while (out.length > 0 && isBlank(out[out.length - 1] as string)) out.pop();
  const last = out.length > 0 ? (out[out.length - 1] as string) : null;
  if (last !== null && (QUOTE_HEADER_DIVIDER_RE.test(last) || BARE_ORIGINAL_RE.test(last))) out.pop();
  return out;
}

// --- "Original message" / "Forwarded message" dividers -------------------------

// The leading `(?<!-)` (here and in LOCALIZED_DIVIDER_RE) lets a match start
// only at the first dash of a run. It changes no result: the wording cannot
// start with a dash, so a match from inside a run has its `-{2,}` end at the
// run's end, and the same match extended to the run's first dash is found
// first. Without it, `search` retried `-{2,}` from every dash of a long rule,
// which is quadratic (a 20,000-dash line took seconds).
const ORIGINAL_MESSAGE_DIVIDER_RE = new PyPattern(
  String.raw`(?<!-)-{2,}[ \t]*(?:(?:Original|Forwarded)[ \t]+Message|邮件原件|原始邮件)[ \t]*-{2,}`,
  { ignoreCase: true },
);

const LOCALIZED_DIVIDER_RE = new PyPattern(
  String.raw`(?<!-)-{2,}${LABEL_SPACE}*(?:` +
    String.raw`Message${LABEL_SPACE}+d['’]origine|Message${LABEL_SPACE}+transf[ée]r[ée]` +
    String.raw`|Mensaje${LABEL_SPACE}+(?:original|reenviado)` +
    String.raw`|Mensagem${LABEL_SPACE}+(?:original|encaminhada)` +
    String.raw`|Messaggio${LABEL_SPACE}+(?:originale|inoltrato)` +
    String.raw`|(?:Oorspronkelijk|Doorgestuurd)${LABEL_SPACE}+bericht` +
    String.raw`|(?:Urspr(?:ü|ue)ngliche|Weitergeleitete)${LABEL_SPACE}+Nachricht` +
    String.raw`)${LABEL_SPACE}*-{2,}`,
  { ignoreCase: true },
);

// The short "Original" dividers of QQ Mail, Foxmail and similar clients
// ("------------------ Original ------------------", "--- Original Message(s)
// ---", the Chinese 邮件原文), the spacing possibly U+00A0, U+3000 or a literal
// "&nbsp;". The `(?<!-)` is the same linear-time guard.
const DIVIDER_SPACE = String.raw`(?:[ \t\u00a0\u3000]|&nbsp;)`;
const SHORT_ORIGINAL_DIVIDER_RE = new PyPattern(
  String.raw`(?<!-)-{2,}${DIVIDER_SPACE}*(?:Original(?:${DIVIDER_SPACE}+Messages?)?|邮件原文)` +
    String.raw`${DIVIDER_SPACE}*-{2,}`,
  { ignoreCase: true },
);
/** A bare "Original" line, drawn by QQ Mail and Foxmail above their quote header. */
const BARE_ORIGINAL_RE = new PyPattern(String.raw`^[ \t]*Original[ \t]*$`);
/** The `From:` (or 发件人) line a bare "Original" line must stand above. */
const BARE_ORIGINAL_FROM_RE = new PyPattern(String.raw`^[ \t*]*(?:From|发件人)[ \t]*[:：]`);
/** A note that the author answers inline ("replies inline", "in-line"). */
const INLINE_NOTE_RE = new PyPattern(String.raw`\bin-?line\b`, { ignoreCase: true });

/**
 * True when `lines[i]` is a bare "Original" line and the first non-blank line
 * among the four below it is a `From:` / 发件人 line.
 */
function isBareOriginalDivider(lines: readonly string[], i: number): boolean {
  if (!BARE_ORIGINAL_RE.test(lines[i] as string)) return false;
  for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
    const line = lines[j] as string;
    if (!isBlank(line)) return BARE_ORIGINAL_FROM_RE.test(line);
  }
  return false;
}

/**
 * The first "Original message" divider in `line`, or `null`. `short: false`
 * leaves out the short "Original" forms.
 */
function findDivider(line: string, localized: boolean, short = true): RegExpExecArray | null {
  let found = ORIGINAL_MESSAGE_DIVIDER_RE.search(line);
  // min(found, key=start): the first of equal starts wins.
  if (short) {
    const m = SHORT_ORIGINAL_DIVIDER_RE.search(line);
    if (m && (found === null || m.index < found.index)) found = m;
  }
  if (localized) {
    const m = LOCALIZED_DIVIDER_RE.search(line);
    // min(found, key=start): the first of equal starts wins.
    if (m && (found === null || m.index < found.index)) found = m;
  }
  return found;
}

/**
 * Truncate everything from a dashed "Original/Forwarded message" divider.
 *
 * A divider on a quote line never truncates; a bare "Original" line above a
 * `From:` line truncates like a divider; once a line above holds an inline note
 * ({@link INLINE_NOTE_RE}), the short forms are ignored.
 */
export function stripAfterOriginalMessageDivider(lines: readonly string[], options: LocalizedOptions = {}): string[] {
  const localized = options.localized ?? true;
  let inlineNote = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] as string;
    if (isQuoteLine(line)) {
      if (!inlineNote && INLINE_NOTE_RE.search(line) !== null) inlineNote = true;
      continue;
    }
    if (!inlineNote && isBareOriginalDivider(lines, i)) return lines.slice(0, i);
    const m = findDivider(line, localized);
    if (m === null) {
      if (!inlineNote && INLINE_NOTE_RE.search(line) !== null) inlineNote = true;
      continue;
    }
    if (inlineNote && findDivider(line, localized, false) === null) continue;
    const out = lines.slice(0, i);
    const prefix = pyRstrip(line.slice(0, m.index));
    if (prefix) out.push(prefix);
    return out;
  }
  return [...lines];
}

// --- Outlook boundary lines -----------------------------------------------------

/** A line Outlook draws above a quoted message: an optional space, then at least seven underscores or hyphens. */
const OUTLOOK_BOUNDARY_RE = new PyPattern(" ?[_-]{7,}");

/** Insert a blank line between a non-empty line and an Outlook boundary line directly below it. */
function separateOutlookBoundaries(lines: readonly string[]): string[] {
  const out: string[] = [];
  for (const line of lines) {
    if (out.length > 0 && out[out.length - 1] && OUTLOOK_BOUNDARY_RE.test(line)) out.push("");
    out.push(line);
  }
  return out;
}

// --- custom cleanup pass ------------------------------------------------------

/** Apply the stage-1 quote/attribution cleanup to `text`. */
export function customClean(text: string, options: LocalizedOptions = {}): string {
  let lines = separateOutlookBoundaries(text.split("\n"));
  lines = stripAfterQuoteHeaderBlock(lines, options);
  lines = stripAfterOriginalMessageDivider(lines, options);
  lines = stripAfterSignoffBoundary(lines);
  lines = stripAttributionLines(lines);
  lines = lines.filter((line) => !isQuoteLine(line));
  // Trim per-line trailing whitespace and blank edge lines, but preserve the
  // first content line's indentation.
  lines = lines.map(pyRstrip);
  let start = 0;
  let end = lines.length;
  while (start < end && !lines[start]) start++;
  while (end > start && !lines[end - 1]) end--;
  return lines.slice(start, end).join("\n");
}

function nonblankLineCount(text: string): number {
  let count = 0;
  for (const line of text.split("\n")) if (!isBlank(line)) count++;
  return count;
}

// --- parent-diff assist -------------------------------------------------------
//
// The input is untrusted, so every step runs in time linear in the size of the
// child and the parent: rule (a) aligns the line sequences around their longest
// common run (alignedRunMarks), and rules (b) and (c) answer their substring
// tests from an index built once per call (ParentStream). See the reference's
// comment block above `_PARENT_DIFF_MIN_BLOCK_WORDS` for the substance bar.

const PARENT_DIFF_MIN_BLOCK_WORDS = 10;
const PARENT_DIFF_MIN_LINE_WORDS = 8;
const PARENT_DIFF_CONTINUATION_MIN_SEEDS = 3;

/** Normalize one line for parent/child comparison. */
export function normalizeLineForDiff(line: string): string {
  return pySplit(QUOTE_RE.sub(line, () => "")).join(" ");
}

// --- suffix automaton -------------------------------------------------------------
//
// One routine serves rules (a), (b) and (c): the suffix automaton of a sequence
// of integer symbols (line ids for rule (a), code points for rules (b) and
// (c)). It recognizes exactly the contiguous subsequences of its text, has at
// most 2n states and 3n transitions for a text of n symbols, and is built in
// O(n) table operations.

/** The reference's automaton tuple `(length, link, trans, first_end)`. */
interface Automaton {
  readonly length: Int32Array;
  readonly link: Int32Array;
  readonly trans: Transitions;
  readonly firstEnd: Int32Array;
}

/**
 * Suffix automaton of `seq[lo:hi]` (the reference's `_build_automaton`).
 *
 * Per state: the length of its longest string, its suffix link, its
 * transitions and `firstEnd`, the smallest end position (an index of `seq`)
 * of an occurrence of the state's strings. State 0 is the root. A clone copies
 * `firstEnd` from the state it splits.
 */
function buildAutomaton(seq: Int32Array, lo: number, hi: number): Automaton {
  const maxStates = 2 * (hi - lo) + 1;
  const length = new Int32Array(maxStates);
  const link = new Int32Array(maxStates);
  const trans = new Transitions(maxStates, 2 * (hi - lo));
  const firstEnd = new Int32Array(maxStates);
  link[0] = -1;
  firstEnd[0] = -1;
  let size = 1;
  let last = 0;
  for (let pos = lo; pos < hi; pos++) {
    const sym = seq[pos] as number;
    const cur = size++;
    length[cur] = (length[last] as number) + 1;
    link[cur] = -1;
    firstEnd[cur] = pos;
    let p = last;
    while (p !== -1 && trans.get(p, sym) < 0) {
      trans.set(p, sym, cur);
      p = link[p] as number;
    }
    if (p === -1) {
      link[cur] = 0;
    } else {
      const q = trans.get(p, sym);
      if ((length[p] as number) + 1 === length[q]) {
        link[cur] = q;
      } else {
        const clone = size++;
        length[clone] = (length[p] as number) + 1;
        link[clone] = link[q] as number;
        trans.copy(q, clone);
        firstEnd[clone] = firstEnd[q] as number;
        while (p !== -1 && trans.get(p, sym) === q) {
          trans.set(p, sym, clone);
          p = link[p] as number;
        }
        link[q] = clone;
        link[cur] = clone;
      }
    }
    last = cur;
  }
  return { length, link, trans, firstEnd };
}

/**
 * For each `e` in `[lo, hi)`: the length of the longest run of `seq` ending at
 * `e` that occurs in the automaton's text, and the state of that run (both
 * indexed `e - lo`). The reference's `_walk`.
 */
function walk(automaton: Automaton, seq: Int32Array, lo: number, hi: number): [Int32Array, Int32Array] {
  const { length, link, trans } = automaton;
  const runs = new Int32Array(hi - lo);
  const states = new Int32Array(hi - lo);
  let state = 0;
  let matched = 0;
  for (let e = lo; e < hi; e++) {
    const sym = seq[e] as number;
    while (state !== 0 && trans.get(state, sym) < 0) {
      state = link[state] as number;
      matched = length[state] as number;
    }
    const next = trans.get(state, sym);
    if (next >= 0) {
      state = next;
      matched += 1;
    } else {
      matched = 0;
    }
    runs[e - lo] = matched;
    states[e - lo] = state;
  }
  return [runs, states];
}

// --- rule (a): alignment around the longest common run ------------------------------
//
// The specification is the comment above the reference's `_intern_lines`.
// Lines compare by exact string equality; the anchor is the longest common run
// (in lines), the earliest in the child among equally long runs, at its
// earliest occurrence in the parent; each side of the anchor is matched only
// against the same side of the parent, in any order.

/**
 * Map each distinct parent line to a small integer id; a child line absent
 * from the parent gets -1, which no transition carries.
 */
function internLines(childSeq: readonly string[], parentSeq: readonly string[]): [Int32Array, Int32Array] {
  const ids = new StringTable();
  const parentIds = new Int32Array(parentSeq.length);
  parentSeq.forEach((line, k) => {
    parentIds[k] = ids.add(line);
  });
  const childIds = new Int32Array(childSeq.length);
  childSeq.forEach((line, k) => {
    childIds[k] = ids.get(line);
  });
  return [childIds, parentIds];
}

/** A child run `[start, end)` has substance (prefix sums of words and of long lines). */
function hasSubstance(wordsBefore: Int32Array, longBefore: Int32Array, start: number, end: number): boolean {
  return (
    (wordsBefore[end] as number) - (wordsBefore[start] as number) >= PARENT_DIFF_MIN_BLOCK_WORDS ||
    (longBefore[end] as number) - (longBefore[start] as number) > 0
  );
}

/**
 * Step 3 for one side: add to `cover` (a difference array over child
 * positions) every run of `childIds[cLo:cHi]` that has substance and occurs in
 * `parentIds[pLo:pHi]`. Only the longest run ending at each position is
 * tested: a shorter one ending there lies inside it.
 */
function coverSide(
  childIds: Int32Array,
  cLo: number,
  cHi: number,
  parentIds: Int32Array,
  pLo: number,
  pHi: number,
  wordsBefore: Int32Array,
  longBefore: Int32Array,
  cover: Int32Array,
): void {
  if (cLo >= cHi || pLo >= pHi) return;
  const [runs] = walk(buildAutomaton(parentIds, pLo, pHi), childIds, cLo, cHi);
  for (let e = cLo; e < cHi; e++) {
    const run = runs[e - cLo] as number;
    if (run === 0) continue;
    const start = e - run + 1;
    if (hasSubstance(wordsBefore, longBefore, start, e + 1)) {
      cover[start] = (cover[start] as number) + 1;
      cover[e + 1] = (cover[e + 1] as number) - 1;
    }
  }
}

/**
 * Rule (a): which child lines (indexes of `childSeq`) are parent content. The
 * reference's `_aligned_run_marks`.
 *
 * @internal
 */
export function alignedRunMarks(childSeq: readonly string[], parentSeq: readonly string[]): boolean[] {
  const count = childSeq.length;
  const marks = new Array<boolean>(count).fill(false);
  if (count === 0 || parentSeq.length === 0) return marks;
  const [childIds, parentIds] = internLines(childSeq, parentSeq);

  // Prefix sums: words, and lines of at least PARENT_DIFF_MIN_LINE_WORDS
  // words, before each child position (a run's substance in O(1)).
  const wordsBefore = new Int32Array(count + 1);
  const longBefore = new Int32Array(count + 1);
  for (let k = 0; k < count; k++) {
    const words = pyWordCount(childSeq[k] as string);
    wordsBefore[k + 1] = (wordsBefore[k] as number) + words;
    longBefore[k + 1] = (longBefore[k] as number) + (words >= PARENT_DIFF_MIN_LINE_WORDS ? 1 : 0);
  }

  // Step 1: the anchor. The first end position reaching the greatest run
  // length (strict comparison) is the earliest-starting longest run; the
  // automaton's firstEnd gives its earliest parent occurrence.
  const automaton = buildAutomaton(parentIds, 0, parentIds.length);
  const [runs, states] = walk(automaton, childIds, 0, count);
  let bestLen = 0;
  let bestEnd = -1;
  for (let e = 0; e < count; e++) {
    if ((runs[e] as number) > bestLen) {
      bestLen = runs[e] as number;
      bestEnd = e;
    }
  }
  if (bestLen === 0) return marks;
  const i = bestEnd - bestLen + 1;
  const j = (automaton.firstEnd[states[bestEnd] as number] as number) - bestLen + 1;

  const cover = new Int32Array(count + 1); // difference array over child positions
  // Step 2: the anchor itself.
  if (hasSubstance(wordsBefore, longBefore, i, i + bestLen)) {
    cover[i] = (cover[i] as number) + 1;
    cover[i + bestLen] = (cover[i + bestLen] as number) - 1;
  }
  // Step 3: each side against the same side of the parent.
  coverSide(childIds, 0, i, parentIds, 0, j, wordsBefore, longBefore, cover);
  coverSide(childIds, i + bestLen, count, parentIds, j + bestLen, parentIds.length, wordsBefore, longBefore, cover);

  let depth = 0;
  for (let k = 0; k < count; k++) {
    depth += cover[k] as number;
    marks[k] = depth > 0;
  }
  return marks;
}

// --- rules (b) and (c): substring tests against the parent's word-stream ---------

/** A UTF-16 high (`low` false) or low surrogate. */
function isSurrogate(code: number, low: boolean): boolean {
  return low ? code >= 0xdc00 && code <= 0xdfff : code >= 0xd800 && code <= 0xdbff;
}

/** No surrogate pair of `s` straddles the code-unit position `pos`. */
function atCodePointBoundary(s: string, pos: number): boolean {
  return !(
    pos > 0 &&
    pos < s.length &&
    isSurrogate(s.charCodeAt(pos - 1), false) &&
    isSurrogate(s.charCodeAt(pos), true)
  );
}

/** The code points of `s`; a lone surrogate is one code point, as in Python. */
function codePoints(s: string): Int32Array {
  const out = new Int32Array(cpLen(s));
  let n = 0;
  for (let i = 0; i < s.length; n++) {
    const cp = codePointAt(s, i);
    out[n] = cp;
    i += cp > 0xffff ? 2 : 1;
  }
  return out;
}

/**
 * Answers `needle in stream` for the parent's word-stream (the parent's
 * normalized non-blank lines joined by single spaces), exactly, in time linear
 * in the needle. The reference's `_ParentStream`; its docstring describes the
 * gram table and the automaton fallback.
 *
 * Units. The reference works in code points. Here the gram table and the
 * comparison at a candidate position use UTF-16 code units, and the automaton
 * uses code points. A unit-level occurrence is a code-point occurrence exactly
 * when no surrogate pair of the stream straddles either of its ends
 * ({@link atCodePointBoundary}); a JavaScript string can hold an unpaired
 * surrogate, which a needle could otherwise match against half of a pair. The
 * length test, an absent gram and a unique gram are exact in either unit, so
 * every answer equals the reference's; only the path that answers may differ.
 *
 * @internal
 */
export class ParentStream {
  private readonly stream: string;
  private table: GramTable | null = null;
  private trans: Transitions | null = null;

  constructor(parentSeq: readonly string[]) {
    this.stream = parentSeq.join(" ");
  }

  private buildTable(): GramTable {
    const stream = this.stream;
    let words = 1;
    for (let at = stream.indexOf(" "); at >= 0; at = stream.indexOf(" ", at + 1)) words++;
    const table = new GramTable(stream, words + 8);
    // stream.split(" ") with a running offset, without building the words. A
    // pair's hash continues from its first word's, so each code unit is
    // hashed twice.
    let offset = 0;
    let end = stream.indexOf(" ");
    if (end < 0) end = stream.length;
    let state = absorb(HASH_START, stream, offset, end);
    for (;;) {
      table.note(offset, end, false, finish(state, end - offset));
      if (end === stream.length) break;
      let nextEnd = stream.indexOf(" ", end + 1);
      if (nextEnd < 0) nextEnd = stream.length;
      table.note(offset, nextEnd, true, finish(absorb(state, stream, end, nextEnd), nextEnd - offset));
      offset = end + 1;
      end = nextEnd;
      state = absorb(HASH_START, stream, offset, end);
    }
    return table;
  }

  /** The needle occurs at code-unit position `start` of the stream. */
  private occursAt(needle: string, start: number): boolean {
    return (
      start >= 0 &&
      this.stream.startsWith(needle, start) &&
      atCodePointBoundary(this.stream, start) &&
      atCodePointBoundary(this.stream, start + needle.length)
    );
  }

  /** The answer for a needle of at least three `words` when the table decides it, else `null`. */
  private decidedByTable(needle: string, words: readonly string[]): boolean | null {
    if (this.table === null) this.table = this.buildTable();
    const table = this.table;
    let count = words.length - 2; // grams: the interior word, or the interior pairs
    if (count > 1) count -= 1;
    const pair = words.length !== 3;
    let candidate = -1;
    let offset = (words[0] as string).length + 1; // the offset of words[k] in the needle
    for (let k = 1; k < 1 + count; k++) {
      const wordLen = (words[k] as string).length;
      const gramEnd = pair ? offset + wordLen + 1 + (words[k + 1] as string).length : offset + wordLen;
      const gram = table.find(needle, offset, gramEnd, pair);
      if (gram < 0) return false;
      const first = table.first(gram);
      const start = first - offset;
      if (first === table.last(gram)) return this.occursAt(needle, start);
      if (candidate < 0 && start >= 0) candidate = start;
      offset += wordLen + 1;
    }
    if (candidate >= 0 && this.occursAt(needle, candidate)) return true;
    return null;
  }

  /** `needle in " ".join(parentSeq)`. */
  contains(needle: string): boolean {
    if (needle.length > this.stream.length) return false;
    const words = needle.split(" ");
    if (words.length >= 3) {
      const decided = this.decidedByTable(needle, words);
      if (decided !== null) return decided;
    }
    if (this.trans === null) {
      const codes = codePoints(this.stream);
      this.trans = buildAutomaton(codes, 0, codes.length).trans;
    }
    const trans = this.trans;
    let state = 0;
    for (let i = 0; i < needle.length; ) {
      const cp = codePointAt(needle, i);
      i += cp > 0xffff ? 2 : 1;
      const next = trans.get(state, cp);
      if (next < 0) return false;
      state = next;
    }
    return true;
  }
}

/**
 * Rule (c): `seeds` extended to the fixpoint of the continuation test. The
 * reference's `_continuation_marks`: an unmarked, unprotected line is marked
 * when an adjacent non-blank line is marked and the two, joined in reading
 * order, occur contiguously in the parent's word-stream. The rule is monotone,
 * so a worklist over the neighbors of newly marked lines reaches the fixpoint.
 */
function continuationMarks(
  childSeq: readonly string[],
  seeds: readonly boolean[],
  isProtected: readonly boolean[],
  parentStream: ParentStream,
): boolean[] {
  const count = childSeq.length;
  const isMarked = seeds.slice();
  const normAt = (k: number): string => childSeq[k] as string;

  /** Unmarked `k` joins a marked neighbor contiguously in the parent. */
  const chains = (k: number): boolean => {
    const norm = normAt(k);
    if (k > 0 && isMarked[k - 1] && parentStream.contains(`${normAt(k - 1)} ${norm}`)) return true;
    if (k + 1 < count && isMarked[k + 1] && parentStream.contains(`${norm} ${normAt(k + 1)}`)) return true;
    return false;
  };

  const pending: number[] = [];
  for (let k = 0; k < count; k++) {
    if (!isMarked[k] && !isProtected[k] && ((k > 0 && isMarked[k - 1]) || (k + 1 < count && isMarked[k + 1]))) {
      pending.push(k);
    }
  }
  for (let k = pending.pop(); k !== undefined; k = pending.pop()) {
    if (isMarked[k] || isProtected[k] || !chains(k)) continue;
    isMarked[k] = true;
    for (const j of [k - 1, k + 1]) {
      if (j >= 0 && j < count && !isMarked[j]) pending.push(j);
    }
  }
  return isMarked;
}

/** The quote-marked lines of `body`, in order, joined by newlines (`_marked_quotation`). */
function markedQuotation(body: string): string {
  return normalizeBody(body).split("\n").filter(isQuoteLine).join("\n");
}

/**
 * Rule (d): which child lines (indexes of `childPairs`) are protected. The
 * reference's `_restored_by_own_quotation`; the comment above it there is the
 * specification. A segment (a maximal run of marked lines within a block of
 * consecutive non-blank lines) is restored when the block's unmarked lines
 * total more words than its marked lines and the quotation stream contains
 * the segment's lines joined by single spaces.
 */
function restoredByOwnQuotation(
  childPairs: readonly (readonly [number, string])[],
  marks: readonly boolean[],
  quotedSeq: readonly string[],
): boolean[] {
  const count = childPairs.length;
  const isProtected: boolean[] = new Array<boolean>(count).fill(false);
  if (count === 0 || quotedSeq.length === 0) return isProtected;
  const indexAt = (k: number): number => (childPairs[k] as readonly [number, string])[0];
  const normAt = (k: number): string => (childPairs[k] as readonly [number, string])[1];

  // Blocks: consecutive indexes in the text. Words per block, by mark.
  const block = new Int32Array(count);
  for (let k = 1; k < count; k++) {
    block[k] = (block[k - 1] as number) + (indexAt(k) === indexAt(k - 1) + 1 ? 0 : 1);
  }
  const blocks = (block[count - 1] as number) + 1;
  const markedWords = new Float64Array(blocks);
  const unmarkedWords = new Float64Array(blocks);
  for (let k = 0; k < count; k++) {
    const words = pyWordCount(normAt(k));
    const b = block[k] as number;
    if (marks[k]) markedWords[b] = (markedWords[b] as number) + words;
    else unmarkedWords[b] = (unmarkedWords[b] as number) + words;
  }

  let quotation: ParentStream | null = null;
  let k = 0;
  while (k < count) {
    if (!marks[k]) {
      k++;
      continue;
    }
    const start = k;
    while (k + 1 < count && marks[k + 1] && block[k + 1] === block[start]) k++;
    const end = k;
    k++;
    const b = block[start] as number;
    if ((unmarkedWords[b] as number) <= (markedWords[b] as number)) continue; // step 2
    if (quotation === null) quotation = new ParentStream(quotedSeq);
    const segment: string[] = [];
    for (let j = start; j <= end; j++) segment.push(normAt(j));
    if (!quotation.contains(segment.join(" "))) continue; // step 1
    for (let j = start; j <= end; j++) isProtected[j] = true;
  }
  return isProtected;
}

// --- marking mode: unmarked parent text the author placed ---------------------
//
// The reference's comment above `_MARKING_MODE_MIN_LINES` is the specification:
// with enough evidence that the client marks its quotation (step 1), unmarked
// lines of a block holding no quote line, above the stop line (step 2), are
// protected (steps 3-4), unless many long runs of them match (step 5).

const MARKING_MODE_MIN_LINES = 3;
const MARKING_MODE_MIN_WORDS = 4;
const MARKING_MODE_SHORT_RUN = 2;
const MARKING_MODE_MANY_RUNS = 8;

/** The leading quote-marker run of a line (ASCII only, no backtracking across lines). */
const QUOTE_DEPTH_RE = /^[ \t]*(?:\.{2,}[ \t]*)?((?:>[ \t]*)+)/;

/** `[depth, content]` of `line` (step 1). */
function quoteDepth(line: string): [number, string] {
  const m = QUOTE_DEPTH_RE.exec(line);
  if (m === null) return [0, pySplit(line).join(" ")];
  let depth = 0;
  for (const ch of m[1] as string) if (ch === ">") depth++;
  return [depth, pySplit(line.slice(m[0].length)).join(" ")];
}

/** Step 1: whether marking mode is on. */
function markingMode(bodyLines: readonly string[], parentBody: string): boolean {
  const parentDepth = new Map<string, number>();
  for (const line of normalizeBody(parentBody).split("\n")) {
    const [depth, content] = quoteDepth(line);
    if (!content) continue;
    const seen = parentDepth.get(content);
    if (seen === undefined || depth < seen) parentDepth.set(content, depth);
  }
  let count = 0;
  for (const line of bodyLines) {
    const [depth, content] = quoteDepth(line);
    if (depth === 0 || !content) continue;
    const seen = parentDepth.get(content);
    if (seen === undefined) continue;
    if (pyWordCount(content) < MARKING_MODE_MIN_WORDS) continue;
    if (depth > seen) count++;
  }
  return count >= MARKING_MODE_MIN_LINES;
}

/** Step 2: the stop line of the marked body `lines`. */
function unmarkedQuoteStart(lines: readonly string[]): number {
  const n = lines.length;
  for (let k = 0; k < n; k++) {
    const line = lines[k] as string;
    if (isBlank(line) || isQuoteLine(line)) continue;
    const end = attributionEndAt(lines, k);
    if (end === null) continue;
    let q = end + 1;
    while (q < n && isBlank(lines[q] as string)) q++;
    if (q < n && !isQuoteLine(lines[q] as string)) return q;
  }
  return n;
}

/** Step 3: the protected normalized lines. */
function markingProtectedLines(bodyLines: readonly string[], stop: number): Set<string> {
  const isProtected = new Set<string>();
  let i = 0;
  while (i < stop) {
    if (isBlank(bodyLines[i] as string)) {
      i++;
      continue;
    }
    let j = i;
    while (j < stop && !isBlank(bodyLines[j] as string)) j++;
    const block = bodyLines.slice(i, j);
    if (!block.some(isQuoteLine)) {
      for (const line of block) isProtected.add(normalizeLineForDiff(line));
    }
    i = j;
  }
  return isProtected;
}

/**
 * Step 5: the runs `[start, end)` that lose their protection. `own[k]` is
 * true for a line protected by step 4 and not by rule (d).
 */
function runsLosingProtection(own: readonly boolean[], marks: readonly boolean[]): [number, number][] {
  const count = own.length;
  const longRuns: [number, number][] = [];
  let k = 0;
  while (k < count) {
    if (!(own[k] && marks[k])) {
      k++;
      continue;
    }
    const start = k;
    while (k < count && own[k] && marks[k]) k++;
    if (k - start > MARKING_MODE_SHORT_RUN) longRuns.push([start, k]);
  }
  return longRuns.length >= MARKING_MODE_MANY_RUNS ? longRuns : [];
}

/** Options of {@link stripParentContent}. */
export interface StripParentOptions {
  /** Apply the continuation rule (default `false`). */
  readonly continuation?: boolean;
  /**
   * The message's marked quotation: the quote-marked lines of its body. When
   * given, the own-quotation rule restores matched runs that it contains.
   */
  readonly quotedText?: string | null;
}

/**
 * Remove lines of `text` that provably came from `parentBody`.
 *
 * Pure; never throws. See the reference's `strip_parent_content` for the
 * aligned-run, rewrap, continuation and own-quotation rules.
 */
export function stripParentContent(text: string, parentBody: string, options: StripParentOptions = {}): string {
  return stripParentContentMarking(text, parentBody, options, null);
}

/**
 * {@link stripParentContent} with marking mode (`_strip_parent_content`):
 * `protectedLines` is the set of step 3 of the marking-mode rules (`null` or
 * empty: marking mode is off).
 */
function stripParentContentMarking(
  text: string,
  parentBody: string,
  options: StripParentOptions,
  protectedLines: ReadonlySet<string> | null,
): string {
  const continuation = options.continuation ?? false;
  const quotedText = options.quotedText ?? null;
  const childRaw = normalizeBody(text).split("\n");
  const childNorm = childRaw.map(normalizeLineForDiff);
  const parentNorm = normalizeBody(parentBody).split("\n").map(normalizeLineForDiff);

  const childPairs: [number, string][] = [];
  childNorm.forEach((norm, i) => {
    if (norm) childPairs.push([i, norm]);
  });
  const childSeq = childPairs.map(([, norm]) => norm);
  const parentSeq = parentNorm.filter((norm) => norm);
  const count = childPairs.length;
  const countTrue = (flags: readonly boolean[]): number => {
    let n = 0;
    for (const flag of flags) if (flag) n++;
    return n;
  };

  // Rule (a): runs of matching lines with enough substance, aligned around
  // the longest common run.
  let seeds: boolean[] = Array.from(alignedRunMarks(childSeq, parentSeq), (flag) => Boolean(flag));

  // Rule (b): a long child line re-wrapped from the parent.
  const parentStream = new ParentStream(parentSeq);
  for (let k = 0; k < count; k++) {
    if (seeds[k]) continue;
    const norm = childSeq[k] as string;
    if (pyWordCount(norm) >= PARENT_DIFF_MIN_LINE_WORDS && parentStream.contains(norm)) seeds[k] = true;
  }

  // Rule (c): chain short remainder lines off marked neighbors, to a fixpoint.
  const unprotected: boolean[] = new Array<boolean>(count).fill(false);
  let marks = seeds;
  if (continuation && countTrue(seeds) >= PARENT_DIFF_CONTINUATION_MIN_SEEDS) {
    marks = continuationMarks(childSeq, seeds, unprotected, parentStream);
  }

  // Rule (d): restore segments the message also carries in its marked
  // quotation, then re-derive rule (c) without them.
  const quotedSeq =
    quotedText === null
      ? []
      : normalizeBody(quotedText)
          .split("\n")
          .map(normalizeLineForDiff)
          .filter((norm) => norm);
  const isProtected = restoredByOwnQuotation(childPairs, marks, quotedSeq);
  if (protectedLines !== null && protectedLines.size > 0) {
    const own: boolean[] = new Array<boolean>(count).fill(false);
    for (let k = 0; k < count; k++) {
      const [i, norm] = childPairs[k] as [number, string];
      if (protectedLines.has(norm) && !isQuoteLine(childRaw[i] as string)) {
        own[k] = !isProtected[k];
        isProtected[k] = true;
      }
    }
    for (const [start, end] of runsLosingProtection(own, marks)) {
      for (let k = start; k < end; k++) isProtected[k] = false;
    }
  }
  if (isProtected.includes(true)) {
    seeds = seeds.map((flag, k) => flag && !isProtected[k]);
    marks = seeds;
    if (continuation && countTrue(seeds) >= PARENT_DIFF_CONTINUATION_MIN_SEEDS) {
      marks = continuationMarks(childSeq, seeds, isProtected, parentStream);
    }
  }

  const marked = new Uint8Array(childRaw.length);
  for (let k = 0; k < count; k++) {
    if (marks[k]) marked[(childPairs[k] as [number, string])[0]] = 1;
  }
  const survivors: string[] = [];
  childRaw.forEach((line, i) => {
    if (!marked[i]) survivors.push(pyRstrip(line));
  });
  return collapseAndTrim(survivors);
}

/** Collapse runs of 2+ blank lines to one and trim blank edges, then join. */
export function collapseAndTrim(lines: readonly string[]): string {
  const collapsed: string[] = [];
  for (const line of lines) {
    if (!line && collapsed.length > 0 && !collapsed[collapsed.length - 1]) continue;
    collapsed.push(line);
  }
  let start = 0;
  let end = collapsed.length;
  while (start < end && !collapsed[start]) start++;
  while (end > start && !collapsed[end - 1]) end--;
  return collapsed.slice(start, end).join("\n");
}

// --- inline replies below a localized quote-header block --------------------------

const INLINE_REPLY_TAG_RE = new PyPattern(
  String.raw`^[ \t]*\[(?!(?:RFC|BCP|STD|FYI|I-D|IANA|draft)\b|(?:RFC|BCP|STD)\d)` +
    String.raw`(?=[^\]\n]*[A-Za-z])[^\[\]\n]{1,20}\]`,
);

const INLINE_REPLY_MIN_PARENT_LINES = 1;

/** Index of the first quote-header block or divider, or `lines.length`. */
function firstQuoteBoundary(lines: readonly string[], localized: boolean): number {
  const found = findQuoteHeaderBlock(lines, { localized });
  const end = found === null ? lines.length : found;
  for (let i = 0; i < end; i++) {
    const line = lines[i] as string;
    if (!isQuoteLine(line) && findDivider(line, localized)) return i;
  }
  return end;
}

/** True when a localized quote-header block or divider sits above the author's tagged inline replies. */
export function localizedInlineReply(lines: readonly string[], parentBody: string | null): boolean {
  const start = firstQuoteBoundary(lines, true);
  const end = firstQuoteBoundary(lines, false);
  if (start >= end) return false;
  const span = lines.slice(start, end).join("\n");
  let survivors: string;
  if (parentBody === null) {
    survivors = span;
  } else {
    survivors = stripParentContent(span, parentBody, { continuation: true });
    const removed = nonblankLineCount(span) - nonblankLineCount(survivors);
    if (removed < INLINE_REPLY_MIN_PARENT_LINES) return false;
  }
  const labels: string[] = [];
  for (const line of survivors.split("\n")) {
    const m = INLINE_REPLY_TAG_RE.match(line);
    if (m) labels.push(pyStrip(m[0]).slice(1, -1));
  }
  if (labels.length === 0) return false;
  return anyAuthorTag(labels, lines.slice(0, start).join("\n"), parentBody);
}

/** The longest inline-reply tag label, in code points (INLINE_REPLY_TAG_RE). */
const MAX_TAG_LABEL = 20;

/** `re`'s Unicode `\w` (one code point), matched with `test` at a set position. */
const WORD_AT_RE = new RegExp(WORD_RE_CLASS, "uy");

/** Python's `\w` on the code point that starts at code-unit position `pos` of `s`. */
function isWordAt(s: string, pos: number): boolean {
  WORD_AT_RE.lastIndex = pos;
  return WORD_AT_RE.test(s);
}

/** The number of code units of the code point that starts at `pos` (a lone surrogate is one). */
function unitsAt(s: string, pos: number): number {
  return isSurrogate(s.charCodeAt(pos), false) && pos + 1 < s.length && isSurrogate(s.charCodeAt(pos + 1), true)
    ? 2
    : 1;
}

/**
 * True when one of the inline-reply tag `labels` belongs to this message's
 * author. The reference's `_any_author_tag`, whose docstring gives the rule.
 *
 * Per label, the tests are `"[" + label in parentBody` and
 * `re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", head)`, answered for
 * all labels together in one scan of each text. Lengths and positions are in
 * code points, as in the reference.
 *
 * @internal
 */
export function anyAuthorTag(labels: readonly string[], head: string, parentBody: string | null): boolean {
  if (parentBody !== null) {
    const found = foundAfterBracket(parentBody, labels);
    for (const label of labels) {
      if (!found.has(label)) return true;
    }
  }
  const names: string[] = [];
  for (const label of labels) {
    const name = pyStrip(pyRstripChars(label, "0123456789"));
    if (name) names.push(name);
  }
  return nameInHead(head, names);
}

/**
 * `strings` (each of 1 to MAX_TAG_LABEL code points) grouped by length: entry
 * `n` of the first list holds those of length `n` (entry 0 is unused); the
 * second lists the lengths present, in increasing order.
 */
function groupByLength(strings: readonly string[]): [StringTable[], number[]] {
  const groups: StringTable[] = [];
  for (let n = 0; n <= MAX_TAG_LABEL; n++) groups.push(new StringTable());
  for (const text of strings) {
    const group = groups[cpLen(text)];
    if (group === undefined) throw new RangeError("label longer than MAX_TAG_LABEL");
    group.add(text);
  }
  const lengths: number[] = [];
  for (let n = 1; n <= MAX_TAG_LABEL; n++) {
    if ((groups[n] as StringTable).size > 0) lengths.push(n);
  }
  return [groups, lengths];
}

/**
 * The labels `label` for which `"[" + label` occurs in `text`. At each `[` of
 * `text`, the slices that follow it are looked up, one per label length
 * present.
 */
function foundAfterBracket(text: string, labels: readonly string[]): StringTable {
  const [groups, lengths] = groupByLength(labels);
  const found = new StringTable();
  let pos = text.indexOf("[");
  while (pos >= 0) {
    // `end` is the code-unit end of the first `taken` code points after the `[`.
    let end = pos + 1;
    let taken = 0;
    for (const n of lengths) {
      while (taken < n && end < text.length) {
        end += unitsAt(text, end);
        taken++;
      }
      if (taken < n) break; // a shorter slice is in no group
      const piece = text.slice(pos + 1, end);
      if ((groups[n] as StringTable).has(piece)) found.add(piece);
    }
    pos = text.indexOf("[", pos + 1);
  }
  return found;
}

/**
 * True when one of `names` occurs in `head` with no word character (Python's
 * `\w`) directly before or after it. At each code point not preceded by a word
 * character, the slice of each name length present is looked up when no word
 * character follows it.
 */
function nameInHead(head: string, names: readonly string[]): boolean {
  if (names.length === 0) return false;
  const [groups, lengths] = groupByLength(names);
  let previousIsWord = false;
  for (let start = 0; start < head.length; start += unitsAt(head, start)) {
    const startIsWord = isWordAt(head, start);
    if (previousIsWord) {
      previousIsWord = startIsWord;
      continue;
    }
    previousIsWord = startIsWord;
    let end = start;
    let taken = 0;
    for (const n of lengths) {
      while (taken < n && end < head.length) {
        end += unitsAt(head, end);
        taken++;
      }
      if (taken < n) break;
      if (end < head.length && isWordAt(head, end)) continue;
      if ((groups[n] as StringTable).has(head.slice(start, end))) return true;
    }
  }
  return false;
}

// --- public entry point -------------------------------------------------------

/** Run the cleanup pass and the parent-diff assist; returns `[text, method]`. */
function coreExtract(body: string, parentBody: string | null): [string, string] {
  const normalized = normalizeBody(body);
  // The marked quotation and the marking-mode reading are taken from the
  // whole body, before any truncation or marking.
  const quotedText = markedQuotation(normalized);
  const bodyLines = normalized.split("\n");
  const marked = markGenericAttributions(normalized);

  // The Latin-script localized blocks and dividers are not applied to an
  // inline reply written below one (see localizedInlineReply).
  const lines = marked.split("\n");
  const localized = !localizedInlineReply(lines, parentBody);
  let text = customClean(marked, { localized });
  let method = "custom";

  // Parent-diff assist: adopted only when it dropped a content line.
  if (parentBody !== null && !isBlank(text)) {
    let protectedLines: Set<string> | null = null;
    if (markingMode(bodyLines, parentBody)) {
      protectedLines = markingProtectedLines(bodyLines, unmarkedQuoteStart(lines));
    }
    const assisted = stripParentContentMarking(text, parentBody, { continuation: true, quotedText }, protectedLines);
    if (nonblankLineCount(assisted) < nonblankLineCount(text)) {
      text = assisted;
      method += "+parent-diff";
    }
  }
  return [text, method];
}

/** Minimum non-blank lines the HTML quoted-text oracle must remove. */
const HTML_QUOTE_MIN_REMOVED = 3;

/** True when `body` looks like a flattened plain part best replaced by HTML. */
function isDegeneratePlain(body: string, htmlNovel: string): boolean {
  const lines = body.split("\n").filter((line) => !isBlank(line));
  if (lines.length === 0 || lines.length >= 4) return false;
  if (!lines.some((line) => cpLen(line) > 400)) return false;
  return nonblankLineCount(htmlNovel) >= 3 * lines.length;
}

const NO_PART_STARTS: readonly number[] = Object.freeze([]);

/** An `ok`/`empty`/`failed` result with no part boundaries. */
function plainResult(text: string, method: string, status: ExtractionStatus): ExtractionResult {
  return Object.freeze({ text, method, status, partStarts: NO_PART_STARTS });
}

/**
 * Wrap a resolved `(method, text)` in a result with the right empty status.
 * `source` is the body the text was derived from (the `text/plain` body, or
 * the full `htmlToText` rendering for the `html-` paths), used only to locate
 * the part boundaries.
 */
function result(method: string, text: string, source: string): ExtractionResult {
  if (isBlank(text)) return plainResult("", method, STATUS_EMPTY);
  return Object.freeze({ text, method, status: STATUS_OK, partStarts: safePartStarts(text, source) });
}

/** The full rendering of `htmlBody` (novel and quoted text); `""` on error. */
function htmlSource(htmlBody: string): string {
  try {
    return htmlToText(htmlBody);
  } catch {
    return "";
  }
}

/** {@link partStarts} over the normalized `source`; empty on any error. */
function safePartStarts(text: string, source: string): readonly number[] {
  try {
    return partStarts(text, normalizeBody(source));
  } catch {
    return NO_PART_STARTS;
  }
}

/**
 * Return the indices of the lines of `text` that begin a new part.
 *
 * `source` is the normalized body `text` was derived from. Every non-blank
 * line of `text` is matched, in order, to the first source line at or after
 * the previous match whose stripped form is equal. A non-blank source line
 * lying between two consecutive matches is content stage 1 removed, and the
 * second line then begins a new part. When a line has no match the alignment
 * is abandoned and the result is empty (a single part). The search pointer
 * only moves forward, so the cost is linear in the two line counts.
 */
function partStarts(text: string, source: string): readonly number[] {
  const src = source.split("\n").map(pyStrip);
  const starts: number[] = [];
  let pos = 0;
  let prev = -1;
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const key = pyStrip(lines[i] as string);
    if (!key) continue;
    let j = pos;
    while (j < src.length && src[j] !== key) j++;
    if (j === src.length) return NO_PART_STARTS;
    if (prev >= 0) {
      for (let k = prev + 1; k < j; k++) {
        if (src[k]) {
          starts.push(i);
          break;
        }
      }
    }
    prev = j;
    pos = j + 1;
  }
  return Object.freeze(starts);
}

/**
 * Extract the author's newly written text from a decoded `text/plain` body
 * (stage 1).
 *
 * Pure and never throws: a `null`/blank body with no usable HTML yields an
 * `empty` result, and any internal error yields `{ text: "", method: "failed",
 * status: "failed" }`. `options.parentBody` enables the parent-diff assist;
 * `options.htmlBody` enables the HTML oracle (HTML-only, degenerate plain
 * fallback, Share-sheet container, or quoted-text assist).
 */
export function extractNewText(body: string | null | undefined, options: ExtractOptions = {}): ExtractionResult {
  try {
    const parentBody = options.parentBody ?? null;
    const htmlBody = options.htmlBody ?? null;
    const parts = htmlBody !== null && !isBlank(htmlBody) ? splitHtmlParts(htmlBody) : null;

    // (a) HTML-only: no usable plain body, but an HTML part to derive from.
    if (body === null || body === undefined || isBlank(body)) {
      if (parts !== null && !isBlank(parts.novelText)) {
        const [text, method] = coreExtract(parts.novelText, parentBody);
        return result(`html-${method}`, text, htmlSource(htmlBody as string));
      }
      return plainResult("", "none", STATUS_EMPTY);
    }

    // (b) Degenerate plain: a flattened plain body the HTML can replace.
    if (parts !== null && isDegeneratePlain(body, parts.novelText)) {
      const [text, method] = coreExtract(parts.novelText, parentBody);
      return result(`html-${method}`, text, htmlSource(htmlBody as string));
    }

    // (d) Share-sheet container: the plain part marks the author's own text as
    // quoted; the HTML's novel text holds it.
    if (parts?.hasShareContainer && !isBlank(parts.novelText)) {
      const [text, method] = coreExtract(parts.novelText, parentBody);
      return result(`html-${method}`, text, htmlSource(htmlBody as string));
    }

    // Normal plain path.
    let [text, method] = coreExtract(body, parentBody);

    // (c) Oracle assist: remove content provably present in the HTML's quoted text.
    if (parts !== null && !isBlank(text) && !isBlank(parts.quotedText)) {
      const assisted = stripParentContent(text, parts.quotedText, { quotedText: markedQuotation(body) });
      if (nonblankLineCount(text) - nonblankLineCount(assisted) >= HTML_QUOTE_MIN_REMOVED) {
        text = assisted;
        method += "+html-quote";
      }
    }
    return result(method, text, body);
  } catch {
    return plainResult("", "failed", STATUS_FAILED);
  }
}
