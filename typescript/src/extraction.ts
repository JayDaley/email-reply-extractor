/**
 * New-text extraction: recover the author's novel content (stage 1 of 2).
 *
 * Port of the reference's `extraction.py`. Its module docstring is the design
 * documentation; docs/extraction.md restructures it. The fragment scanner
 * ("ERP") is in {@link readFragments}; the HTML oracle in {@link splitHtmlParts}.
 *
 * @module
 */

import { SequenceMatcher } from "./difflib.js";
import { readFragments } from "./fragments.js";
import { splitHtmlParts } from "./htmlText.js";
import {
  cpLen,
  escapeRegex,
  firstIsUpper,
  hasPyDigit,
  isBlank,
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
 * The generation of the text-deriving routine (this module, `cleaning`,
 * `htmlText` and `fragments` together, and their counterparts in the other
 * ports). Consumers stamp stored derived text with it and compare with `<`, so
 * it only ever increases. See docs/versioning.md.
 */
export const EXTRACTION_VERSION = 6;

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
   * Which path produced the text: `none`, `erp`, `erp+custom`,
   * `custom-fallback` or `failed`, with an optional `html-` prefix and
   * `+parent-diff` / `+html-quote` suffixes.
   */
  readonly method: string;
  /** One of `ok`, `empty`, `failed`. */
  readonly status: ExtractionStatus;
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

/** BOM / zero-width space / zero-width non-joiner / zero-width joiner / word-joiner. */
// biome-ignore lint/complexity/useRegexLiterals: escapes keep the invisible characters readable
const ZERO_WIDTH_RE = new RegExp("[\\ufeff\\u200b\\u200c\\u200d\\u2060]", "gu");

/** Normalize line endings and strip BOM / zero-width characters. */
export function normalizeBody(body: string): string {
  return body.replaceAll("\r\n", "\n").replaceAll("\r", "\n").replace(ZERO_WIDTH_RE, "");
}

// --- line classifiers ---------------------------------------------------------

const QUOTE_RE = new PyPattern(String.raw`^[ \t]*(?:\.{2,}[ \t]*)?>+`);

/** True for a quoted line (any indentation; also the `...>` flowed form). */
export function isQuoteLine(line: string): boolean {
  return QUOTE_RE.test(line);
}

// --- attribution lines --------------------------------------------------------

const ATTRIBUTION_RES: readonly PyPattern[] = [
  new PyPattern(String.raw`^[ \t]*On\b.*wrote:[ \t]*$`),
  new PyPattern(String.raw`^.*<[^>]+@[^>]+>[ \t]+wrote:[ \t]*$`),
  new PyPattern(String.raw`^[ \t]*Am\b.*\bschrieb\b.*:[ \t]*$`),
  new PyPattern(String.raw`^[ \t]*\d{4}(?:年|[/.-]).*のメール:[ \t]*$`),
];

const ATTRIBUTION_START_RE = new PyPattern(String.raw`^[ \t]*(?:(?:On|Am|Le|El|El día)\b|\d{4}(?:年|[/.-]))`);
const ATTRIBUTION_END_RE = new PyPattern(
  String.raw`(?:wrote:|\bschrieb\b.*:|\ba écrit\s*:|\bescribió:|のメール:)[ \t]*$`,
);

/** True if `line` on its own is a complete attribution line. */
export function isAttributionLine(line: string): boolean {
  return ATTRIBUTION_RES.some((pat) => pat.test(line));
}

/**
 * If `lines[i]` opens a wrapped attribution whose terminator lands on the next
 * line or two, with no blank between, return the terminator line's index.
 */
export function wrappedAttributionEnd(lines: readonly string[], i: number): number | null {
  const line = lines[i] as string;
  if (!ATTRIBUTION_START_RE.test(line) || ATTRIBUTION_END_RE.search(line)) return null;
  for (let j = i + 1; j < Math.min(i + 3, lines.length); j++) {
    const next = lines[j] as string;
    if (isBlank(next)) return null;
    if (ATTRIBUTION_END_RE.search(next)) return j;
  }
  return null;
}

/** Drop attribution lines, including forms wrapped across up to two lines. */
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

// --- sign-off boundary ----------------------------------------------------------

/** Sign-off salutations, ordered longest-first (shared with `cleaning`). */
const SIGNOFF_SALUTATIONS =
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

/** A line that is only a sign-off salutation (optional trailing `,` `.` or `!`). */
export const SIGNOFF_LINE_RE = new PyPattern(String.raw`^[ \t]*(?:${SIGNOFF_SALUTATIONS})[ \t]*[,.!]?[ \t]*$`, {
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
 * followed by an attribution line, or `null`.
 */
export function findSignoffBoundary(lines: readonly string[]): number | null {
  const n = lines.length;
  for (let i = 0; i < n; i++) {
    const nameIdx = signoffNameIndex(lines, i);
    if (nameIdx === null) continue;
    let k = nameIdx + 1;
    while (k < n && isBlank(lines[k] as string)) k++;
    if (k >= n) continue;
    if (isAttributionLine(lines[k] as string) || wrappedAttributionEnd(lines, k) !== null) {
      return nameIdx + 1;
    }
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

/** True when the contiguous header-field run above `lines[i]` contains a transport field. */
function inPastedHeaderRun(lines: readonly string[], i: number): boolean {
  let j = i - 1;
  while (j >= 0 && HEADER_FIELD_RE.test(lines[j] as string)) {
    if (TRANSPORT_HEADER_RE.test(lines[j] as string)) return true;
    j--;
  }
  return false;
}

/**
 * Return the index of the `From:` line that opens a forwarded/quote-header
 * block, or `null` if the body has no such block. `localized: false`
 * recognizes only the English, German-label and Chinese blocks.
 */
export function findQuoteHeaderBlock(lines: readonly string[], options: LocalizedOptions = {}): number | null {
  const localized = options.localized ?? true;
  const n = lines.length;
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
    if (i > 0 && inPastedHeaderRun(lines, i)) continue;

    const isField = (text: string): boolean =>
      HEADER_FIELD_RE.test(text) || (isLocalized && LOCALIZED_FIELD_RE.test(text));

    const tolerant = line.includes("@");
    let headerLines = 1;
    let hasSignal = false;
    let j = i + 1;
    while (j < n) {
      const nxt = lines[j] as string;
      if (isField(nxt)) {
        headerLines++;
        if (isLocalized ? LOCALIZED_SUBJECT_RE.test(nxt) : QUOTE_HEADER_SIGNAL_RE.test(nxt)) hasSignal = true;
        j++;
      } else if (!isBlank(nxt) && (tolerant || nxt.charAt(0) === " " || nxt.charAt(0) === "\t")) {
        // Folded continuation of the preceding header field.
        j++;
      } else if (isBlank(nxt) && tolerant && j + 1 < n && isField(lines[j + 1] as string)) {
        // Double-spaced rendering: a single blank between fields.
        j++;
      } else {
        break;
      }
    }
    if (hasSignal && headerLines >= (isLocalized ? 3 : 2)) return i;
  }
  return null;
}

/** The separator Alibaba Mail draws directly above its quote-header block. */
const QUOTE_HEADER_DIVIDER_RE = new PyPattern(String.raw`^[ \t]*-{6,}[ \t]*$`);

/** Truncate everything from a forwarded/quote-header block to the end. */
export function stripAfterQuoteHeaderBlock(lines: readonly string[], options: LocalizedOptions = {}): string[] {
  const idx = findQuoteHeaderBlock(lines, options);
  if (idx === null) return [...lines];
  const out = lines.slice(0, idx);
  while (out.length > 0 && isBlank(out[out.length - 1] as string)) out.pop();
  if (out.length > 0 && QUOTE_HEADER_DIVIDER_RE.test(out[out.length - 1] as string)) out.pop();
  return out;
}

// --- "Original message" / "Forwarded message" dividers -------------------------

const ORIGINAL_MESSAGE_DIVIDER_RE = new PyPattern(
  String.raw`-{2,}[ \t]*(?:(?:Original|Forwarded)[ \t]+Message|邮件原件|原始邮件)[ \t]*-{2,}`,
  { ignoreCase: true },
);

const LOCALIZED_DIVIDER_RE = new PyPattern(
  String.raw`-{2,}${LABEL_SPACE}*(?:` +
    String.raw`Message${LABEL_SPACE}+d['’]origine|Message${LABEL_SPACE}+transf[ée]r[ée]` +
    String.raw`|Mensaje${LABEL_SPACE}+(?:original|reenviado)` +
    String.raw`|Mensagem${LABEL_SPACE}+(?:original|encaminhada)` +
    String.raw`|Messaggio${LABEL_SPACE}+(?:originale|inoltrato)` +
    String.raw`|(?:Oorspronkelijk|Doorgestuurd)${LABEL_SPACE}+bericht` +
    String.raw`|(?:Urspr(?:ü|ue)ngliche|Weitergeleitete)${LABEL_SPACE}+Nachricht` +
    String.raw`)${LABEL_SPACE}*-{2,}`,
  { ignoreCase: true },
);

/** The first "Original message" divider in `line`, or `null`. */
function findDivider(line: string, localized: boolean): RegExpExecArray | null {
  let found = ORIGINAL_MESSAGE_DIVIDER_RE.search(line);
  if (localized) {
    const m = LOCALIZED_DIVIDER_RE.search(line);
    // min(found, key=start): the first of equal starts wins.
    if (m && (found === null || m.index < found.index)) found = m;
  }
  return found;
}

/** Truncate everything from a dashed "Original/Forwarded message" divider. */
export function stripAfterOriginalMessageDivider(lines: readonly string[], options: LocalizedOptions = {}): string[] {
  const localized = options.localized ?? true;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] as string;
    const m = findDivider(line, localized);
    if (m === null) continue;
    const out = lines.slice(0, i);
    const prefix = pyRstrip(line.slice(0, m.index));
    if (prefix) out.push(prefix);
    return out;
  }
  return [...lines];
}

// --- custom cleanup pass ------------------------------------------------------

/** Apply the stage-1 quote/attribution cleanup to `text`. */
export function customClean(text: string, options: LocalizedOptions = {}): string {
  let lines = text.split("\n");
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

// --- over-strip guard ---------------------------------------------------------

/** Fraction of plainly unquoted content lines the primary path must keep. */
const GUARD_MIN_RETAINED = 0.6;

/** Count non-blank body lines that are plainly authored content. */
export function countUnquotedContentLines(body: string, options: LocalizedOptions = {}): number {
  let lines = stripAfterQuoteHeaderBlock(body.split("\n"), options);
  lines = stripAfterOriginalMessageDivider(lines, options);
  lines = stripAfterSignoffBoundary(lines);
  let count = 0;
  for (const line of lines) {
    if (isBlank(line)) continue;
    if (isQuoteLine(line) || isAttributionLine(line)) continue;
    count++;
  }
  return count;
}

function nonblankLineCount(text: string): number {
  let count = 0;
  for (const line of text.split("\n")) if (!isBlank(line)) count++;
  return count;
}

// --- parent-diff assist -------------------------------------------------------

const PARENT_DIFF_MIN_BLOCK_WORDS = 10;
const PARENT_DIFF_MIN_LINE_WORDS = 8;
const PARENT_DIFF_CONTINUATION_MIN_SEEDS = 3;

/** Normalize one line for parent/child comparison. */
export function normalizeLineForDiff(line: string): string {
  return pySplit(QUOTE_RE.sub(line, () => "")).join(" ");
}

/** Options of {@link stripParentContent}. */
export interface StripParentOptions {
  /** Apply the continuation rule (default `false`). */
  readonly continuation?: boolean;
}

/**
 * Remove lines of `text` that provably came from `parentBody`.
 *
 * Pure; never throws. See the reference's `strip_parent_content` for the
 * aligned-run, rewrap and continuation rules.
 */
export function stripParentContent(text: string, parentBody: string, options: StripParentOptions = {}): string {
  const continuation = options.continuation ?? false;
  const childRaw = normalizeBody(text).split("\n");
  const childNorm = childRaw.map(normalizeLineForDiff);
  const parentNorm = normalizeBody(parentBody).split("\n").map(normalizeLineForDiff);

  const childPairs: [number, string][] = [];
  childNorm.forEach((norm, i) => {
    if (norm) childPairs.push([i, norm]);
  });
  const childSeq = childPairs.map(([, norm]) => norm);
  const parentSeq = parentNorm.filter((norm) => norm);

  const marked = new Set<number>();

  // Rule (a): aligned runs of matching lines with enough substance.
  const matcher = new SequenceMatcher(childSeq, parentSeq);
  for (const [start, , size] of matcher.getMatchingBlocks()) {
    if (size === 0) continue;
    const block = childSeq.slice(start, start + size);
    let totalWords = 0;
    let maxLineWords = 0;
    for (const line of block) {
      const words = pyWordCount(line);
      totalWords += words;
      if (words > maxLineWords) maxLineWords = words;
    }
    if (totalWords >= PARENT_DIFF_MIN_BLOCK_WORDS || maxLineWords >= PARENT_DIFF_MIN_LINE_WORDS) {
      for (let k = start; k < start + size; k++) marked.add((childPairs[k] as [number, string])[0]);
    }
  }

  // Rule (b): a long child line re-wrapped from the parent.
  const parentStream = parentSeq.join(" ");
  for (const [origI, norm] of childPairs) {
    if (marked.has(origI)) continue;
    if (pyWordCount(norm) >= PARENT_DIFF_MIN_LINE_WORDS && parentStream.includes(norm)) marked.add(origI);
  }

  // Rule (c): chain short remainder lines off marked neighbors, to a fixpoint.
  let changed = continuation && marked.size >= PARENT_DIFF_CONTINUATION_MIN_SEEDS;
  while (changed) {
    changed = false;
    for (let k = 0; k < childPairs.length; k++) {
      const [origI, norm] = childPairs[k] as [number, string];
      if (marked.has(origI)) continue;
      if (k > 0) {
        const [prevI, prevNorm] = childPairs[k - 1] as [number, string];
        if (marked.has(prevI) && parentStream.includes(`${prevNorm} ${norm}`)) {
          marked.add(origI);
          changed = true;
          continue;
        }
      }
      if (k + 1 < childPairs.length) {
        const [nextI, nextNorm] = childPairs[k + 1] as [number, string];
        if (marked.has(nextI) && parentStream.includes(`${norm} ${nextNorm}`)) {
          marked.add(origI);
          changed = true;
        }
      }
    }
  }

  const survivors: string[] = [];
  childRaw.forEach((line, i) => {
    if (!marked.has(i)) survivors.push(pyRstrip(line));
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
    if (findDivider(lines[i] as string, localized)) return i;
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
  const head = lines.slice(0, start).join("\n");
  for (const line of survivors.split("\n")) {
    const m = INLINE_REPLY_TAG_RE.match(line);
    if (m && isAuthorTag(pyStrip(m[0]).slice(1, -1), head, parentBody)) return true;
  }
  return false;
}

/** True when the inline-reply tag `label` belongs to this message's author. */
export function isAuthorTag(label: string, head: string, parentBody: string | null): boolean {
  if (parentBody !== null && !parentBody.includes(`[${label}`)) return true;
  const name = pyStrip(pyRstripChars(label, "0123456789"));
  if (!name) return false;
  const re = new RegExp(`(?<!${WORD_RE_CLASS})${escapeRegex(name)}(?!${WORD_RE_CLASS})`, "u");
  return re.test(head);
}

// --- public entry point -------------------------------------------------------

/** Run the ERP + custom + over-strip-guard + parent-diff pipeline; returns `[text, method]`. */
function coreExtract(body: string, parentBody: string | null): [string, string] {
  let normalized = normalizeBody(body);

  // Truncate at a forwarded/quote-header block before ERP (see the reference).
  const lines = normalized.split("\n");
  const localized = !localizedInlineReply(lines, parentBody);
  const pretruncated = findQuoteHeaderBlock(lines, { localized }) !== null;
  if (pretruncated) normalized = stripAfterQuoteHeaderBlock(lines, { localized }).join("\n");

  // Primary: join every ERP fragment that is not quoted, signatures included.
  const erpOut = readFragments(normalized)
    .filter((frag) => !frag.quoted)
    .map((frag) => frag.content)
    .join("\n");
  const cleaned = customClean(erpOut, { localized });
  const customChanged = pretruncated || cleaned !== pyStrip(erpOut);

  // Over-strip guard.
  const unquoted = countUnquotedContentLines(normalized, { localized });
  let text: string;
  let method: string;
  if (unquoted > 0 && nonblankLineCount(cleaned) < GUARD_MIN_RETAINED * unquoted) {
    const fallback = customClean(normalized, { localized });
    if (!isBlank(fallback)) {
      text = fallback;
      method = "custom-fallback";
    } else {
      text = cleaned;
      method = customChanged ? "erp+custom" : "erp";
    }
  } else {
    text = cleaned;
    method = customChanged ? "erp+custom" : "erp";
  }

  // Parent-diff assist, after guard resolution.
  if (parentBody !== null && !isBlank(text)) {
    const assisted = stripParentContent(text, parentBody, { continuation: true });
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

function result(method: string, text: string): ExtractionResult {
  if (isBlank(text)) return Object.freeze({ text: "", method, status: STATUS_EMPTY });
  return Object.freeze({ text, method, status: STATUS_OK });
}

/**
 * Extract the author's newly written text from a decoded `text/plain` body
 * (stage 1).
 *
 * Pure and never throws: a `null`/blank body with no usable HTML yields an
 * `empty` result, and any internal error yields `{ text: "", method: "failed",
 * status: "failed" }`. `options.parentBody` enables the parent-diff assist;
 * `options.htmlBody` enables the HTML oracle (HTML-only, degenerate plain
 * fallback, or quoted-text assist).
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
        return result(`html-${method}`, text);
      }
      return Object.freeze({ text: "", method: "none", status: STATUS_EMPTY });
    }

    // (b) Degenerate plain: a flattened plain body the HTML can replace.
    if (parts !== null && isDegeneratePlain(body, parts.novelText)) {
      const [text, method] = coreExtract(parts.novelText, parentBody);
      return result(`html-${method}`, text);
    }

    // Normal plain path.
    let [text, method] = coreExtract(body, parentBody);

    // (c) Oracle assist: remove content provably present in the HTML's quoted text.
    if (parts !== null && !isBlank(text) && !isBlank(parts.quotedText)) {
      const assisted = stripParentContent(text, parts.quotedText);
      if (nonblankLineCount(text) - nonblankLineCount(assisted) >= HTML_QUOTE_MIN_REMOVED) {
        text = assisted;
        method += "+html-quote";
      }
    }
    return result(method, text);
  } catch {
    return Object.freeze({ text: "", method: "failed", status: STATUS_FAILED });
  }
}
