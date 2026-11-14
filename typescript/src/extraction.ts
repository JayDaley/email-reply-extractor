/**
 * New-text extraction: recover the author's novel content (stage 1 of 2).
 *
 * Port of the reference's `extraction.py`. Its module docstring is the design
 * documentation; docs/extraction.md restructures it. The fragment scanner
 * ("ERP") is in {@link readFragments}; the HTML oracle in {@link splitHtmlParts}.
 *
 * @module
 */

import { readFragments } from "./fragments.js";
import { absorb, finish, GramTable, HASH_START, StringTable, Transitions } from "./hashTables.js";
import { splitHtmlParts } from "./htmlText.js";
import {
  codePointAt,
  cpLen,
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
export const EXTRACTION_VERSION = 7;

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

// Two forms are not run as single regexes, because the engine's backtracking
// over them is polynomial on adversarial lines (a line of "<a@" repeated, or of
// "schrieb:" repeated): "<who> <a@b> wrote:" ({@link isAddressAttribution}) and
// the German "Am <date> schrieb <who>:" ({@link isGermanAttribution}). Each is
// decided by a linear helper that accepts exactly the strings the original
// pattern (given in its comment) accepts under `re.match`.
const ATTRIBUTION_RES: readonly PyPattern[] = [
  new PyPattern(String.raw`^[ \t]*On\b.*wrote:[ \t]*$`),
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

const ATTRIBUTION_START_RE = new PyPattern(String.raw`^[ \t]*(?:(?:On|Am|Le|El|El día)\b|\d{4}(?:年|[/.-]))`);
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

/** True if `line` on its own is a complete attribution line. */
export function isAttributionLine(line: string): boolean {
  return ATTRIBUTION_RES.some((pat) => pat.test(line)) || isAddressAttribution(line) || isGermanAttribution(line);
}

/**
 * If `lines[i]` opens a wrapped attribution whose terminator lands on the next
 * line or two, with no blank between, return the terminator line's index.
 */
export function wrappedAttributionEnd(lines: readonly string[], i: number): number | null {
  const line = lines[i] as string;
  if (!ATTRIBUTION_START_RE.test(line) || hasAttributionEnd(line)) return null;
  for (let j = i + 1; j < Math.min(i + 3, lines.length); j++) {
    const next = lines[j] as string;
    if (isBlank(next)) return null;
    if (hasAttributionEnd(next)) return j;
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

  // The reference's `marked` set of child line indexes.
  const marked = new Uint8Array(childRaw.length);
  let markedCount = 0;
  const mark = (i: number): void => {
    if (marked[i] === 0) {
      marked[i] = 1;
      markedCount++;
    }
  };

  // Rule (a): runs of matching lines with enough substance, aligned around
  // the longest common run.
  const ruleA = alignedRunMarks(childSeq, parentSeq);
  for (let k = 0; k < childPairs.length; k++) {
    if (ruleA[k]) mark((childPairs[k] as [number, string])[0]);
  }

  // Rule (b): a long child line re-wrapped from the parent.
  const parentStream = new ParentStream(parentSeq);
  for (const [origI, norm] of childPairs) {
    if (marked[origI]) continue;
    if (pyWordCount(norm) >= PARENT_DIFF_MIN_LINE_WORDS && parentStream.contains(norm)) mark(origI);
  }

  // Rule (c): chain short remainder lines off marked neighbors, to a fixpoint.
  // The rule is monotone (a mark only ever enables more marks), so its
  // fixpoint is the same whatever order lines are examined in. A worklist
  // re-examines only the neighbors of a newly marked line; repeating full
  // passes until nothing changed needed one pass per backward-chained mark.
  if (continuation && markedCount >= PARENT_DIFF_CONTINUATION_MIN_SEEDS) {
    const count = childPairs.length;
    const isMarked = childPairs.map(([origI]) => marked[origI] === 1);
    const normAt = (k: number): string => (childPairs[k] as [number, string])[1];

    /** Unmarked `k` joins a marked neighbor contiguously in the parent. */
    const chains = (k: number): boolean => {
      const norm = normAt(k);
      if (k > 0 && isMarked[k - 1] && parentStream.contains(`${normAt(k - 1)} ${norm}`)) return true;
      if (k + 1 < count && isMarked[k + 1] && parentStream.contains(`${norm} ${normAt(k + 1)}`)) return true;
      return false;
    };

    const pending: number[] = [];
    for (let k = 0; k < count; k++) {
      if (!isMarked[k] && ((k > 0 && isMarked[k - 1]) || (k + 1 < count && isMarked[k + 1]))) pending.push(k);
    }
    for (let k = pending.pop(); k !== undefined; k = pending.pop()) {
      if (isMarked[k] || !chains(k)) continue;
      isMarked[k] = true;
      mark((childPairs[k] as [number, string])[0]);
      for (const j of [k - 1, k + 1]) {
        if (j >= 0 && j < count && !isMarked[j]) pending.push(j);
      }
    }
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
