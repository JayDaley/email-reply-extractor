/**
 * Cleaning: prepare extracted new text for scoring (stage 2 of 2).
 *
 * Port of the reference's `cleaning.py`; its module docstring documents the
 * removal steps and their order. {@link cleanForScoring} strips greetings,
 * sign-offs, signature blocks, list footers, mobile taglines and trailing
 * disclaimers from stage-1 text, and reports the indices of the non-blank
 * input lines it removed.
 *
 * @module
 */

import {
  collapseAndTrim,
  dollarLine,
  endsWithBracketedAddress,
  isSignoffNameLike,
  isSignoffNameLine,
  normalizeLineForDiff,
  SIGNOFF_LINE_RE,
  signoffNameIndex,
} from "./extraction.js";
import { hasPyDigit, isBlank, PyPattern, pyRstrip, pyWordCount } from "./pyCompat.js";

// --- result -------------------------------------------------------------------

/** The outcome of {@link cleanForScoring} on one stage-1 extraction. */
export interface CleanResult {
  /** The furniture-free text. */
  readonly text: string;
  /**
   * 0-based indices into the input text's `split("\n")` of every non-blank
   * line the clean pass removed.
   */
  readonly ignoredLines: readonly number[];
}

/** Options of {@link cleanForScoring}. */
export interface CleanOptions {
  /** The visible text of the message's HTML signature container (`splitHtmlParts(html).signatureText`). */
  readonly htmlSignatureText?: string | null;
}

// --- signature delimiter ------------------------------------------------------

const SIG_DELIM_RE = new PyPattern(String.raw`^--[ \t]*$`);

/** True for the `-- ` signature delimiter (not "--eh." or "-markku"). */
export function isSignatureDelimiter(line: string): boolean {
  return SIG_DELIM_RE.test(line);
}

const CUSTOM_SIG_DIVIDER_RE = new PyPattern(String.raw`^[ \t]*[=~*#_]{3,}[ \t]*$`);

// --- individually droppable signature-debris lines ----------------------------

const TILDE_SIGNOFF_RE = new PyPattern(String.raw`^[ \t]*~[ \t]+\S`);

// A titled personal contact line that is only a name and a bracketed email. The
// full rule is `^[ \t]*(?:Dr|…|Prof\.-Dr)\.?[ \t]+\S.*<[^>]+@[^>]+>[ \t]*$`; only
// the prefix up to `\S` is a regex (isContactLine decides the rest), because
// the engine's backtracking over `.*<[^>]+@[^>]+>` is cubic on a line of "<a@"
// repeated.
const CONTACT_PREFIX_RE = new PyPattern(
  String.raw`^[ \t]*(?:Dr|Prof|Mr|Mrs|Ms|Miss|PhD|Herr|Frau|Sir|Dame|Prof\.-Dr)\.?[ \t]+\S`,
);
/** The tail of the contact rule, applied with `match` just after the `>`. */
const CONTACT_TAIL_RE = new PyPattern(String.raw`[ \t]*$`);

/**
 * True for a titled contact line (the full rule above), in linear time.
 *
 * The prefix regex has a single way to match: `[ \t]+\S` must take the whole
 * blank run, and no honorific followed by an optional `.` and a blank is a
 * prefix of another one followed the same way. The rest is
 * {@link endsWithBracketedAddress} from just after the `\S` (in UTF-16 units:
 * the end of the match, so a non-BMP `\S` is consumed whole).
 */
function isContactLine(line: string): boolean {
  const prefix = CONTACT_PREFIX_RE.match(line);
  return prefix !== null && endsWithBracketedAddress(line, prefix.index + prefix[0].length, CONTACT_TAIL_RE);
}

const PHONE_LINE_RE = new PyPattern(
  String.raw`\b(?:tel|telephone|phone|mobile|cell|fax|toll[ -]?free)\b[.:]?[ \t]*[+(]?\d[\d \t().x/-]{5,}`,
  { ignoreCase: true },
);

const CONTACT_CUE_RE = new PyPattern(
  String.raw`\bwww\.|https?://` +
    String.raw`|\b(?:tel|telephone|phone|mobile|cell|fax|toll[ -]?free)\b` +
    String.raw`|\b(?:blvd|boulevard|street|ave|avenue|suite|floor|road|drive|lane)\b` +
    String.raw`|\b[A-Za-z]\d[A-Za-z][ \t]?\d[A-Za-z]\d\b` +
    String.raw`|,[ \t]*[A-Z]{2}[ \t]+\d{5}\b`,
  { ignoreCase: true },
);

const IDENTIFIER_LINE_RE = new PyPattern(
  String.raw`^[ \t]*(?:(?-i:[A-Z])[\w.()&-]*[ \t]+){0,2}` +
    String.raw`(?:ORCID|LinkedIn|GitHub|GitLab|Mastodon|Bluesky|Twitter|Skype|Signal` +
    String.raw`|Telegram|WhatsApp|WeChat|Matrix|IRC|Website?|Homepage|Blog|E-?mail|D-?U-?N-?S)` +
    String.raw`[ \t]*(?::[ \t]*\S|<https?://[^>]+>[ \t]*$)`,
  { ignoreCase: true },
);

// A labeled office / postal address line. The reference's pattern ends in
// `:[ \t]*[^\n]*\d`; the `[ \t]*` is dropped here because `[^\n]*` already
// accepts every blank, so the two accept the same strings, but adjacent they
// split a long blank run in quadratically many ways before a line without a
// digit fails (a 40,000-blank line took seconds). The pattern is only used
// with `test`, so no group or span depends on the split.
const ADDRESS_LINE_RE = new PyPattern(String.raw`^[ \t]*(?:\S+[ \t]+){0,2}(?:Office|Address)[ \t]*:[^\n]*\d`, {
  ignoreCase: true,
});

// Mailing-list footer debris. The full rule is
// `^[ \t]*(?:_{5,}|.*\bmailing list\b.*--.*|To unsubscribe\b.*)[ \t]*$`
// (IGNORECASE); isListFooter decides the middle alternative without the regex
// engine, whose backtracking over `.*….*--.*` is quadratic on a line of
// "mailing list" repeated.
const LIST_FOOTER_RE = new PyPattern(String.raw`^[ \t]*(?:_{5,}|To unsubscribe\b.*)[ \t]*$`, {
  ignoreCase: true,
});
const MAILING_LIST_RE = new PyPattern(String.raw`\bmailing list\b`, { ignoreCase: true });

/**
 * True for a mailing-list footer line (the full rule above), in linear time.
 *
 * No part of the rule can match `\n`, so it can only match within
 * {@link dollarLine}'s span; checking that first also keeps the `.*[ \t]*$` of
 * the regex from backtracking over a line it cannot match. Within the span,
 * `.*` can skip anything, so the middle alternative holds exactly when a `--`
 * follows the end of the first "mailing list".
 */
function isListFooter(line: string): boolean {
  const span = dollarLine(line);
  if (span === null) return false;
  if (LIST_FOOTER_RE.test(line)) return true;
  const found = MAILING_LIST_RE.search(span);
  return found !== null && span.indexOf("--", found.index + found[0].length) >= 0;
}

const PGP_RE = new PyPattern(String.raw`^[ \t]*(?:-----(?:BEGIN|END) PGP|PGP key-(?:id|fingerprint)\b)`, {
  ignoreCase: true,
});

/** True for a corporate-signature contact line, dropped individually. */
function isContactSignatureLine(line: string): boolean {
  if (PHONE_LINE_RE.search(line)) return true;
  return line.includes("|") && CONTACT_CUE_RE.search(line) !== null;
}

/** True for a line that is itself signature debris but not a text boundary. */
export function isDroppableSignatureLine(line: string): boolean {
  return (
    TILDE_SIGNOFF_RE.test(line) ||
    isContactLine(line) ||
    IDENTIFIER_LINE_RE.test(line) ||
    ADDRESS_LINE_RE.test(line) ||
    isListFooter(line) ||
    PGP_RE.test(line) ||
    isContactSignatureLine(line)
  );
}

// --- trailing sign-off debris -------------------------------------------------

const LINK_ONLY_LINE_RE = new PyPattern(
  String.raw`^[ \t]*(?:` +
    String.raw`https?://\S+` +
    String.raw`|www\.\S+` +
    String.raw`|[\w.+-]+@[\w-]+(?:\.[\w-]+)+` +
    String.raw`|(?:[\w-]+\.){1,6}[A-Za-z]{2,24}(?:/\S*)?` +
    String.raw`)[ \t]*$`,
);

const LABELED_LINK_LINE_RE = new PyPattern(String.raw`^[ \t]*[^:\n]{1,40}:[ \t]*(?:https?://|www\.)\S+[ \t]*$`);

/** Drop personal-signature furniture trailing a closing sign-off. */
export function stripTrailingSignoffDebris(lines: readonly string[]): string[] {
  const n = lines.length;
  // Facts about every tail lines[p:], built on the first anchor in one backward
  // pass, so each anchor is decided in constant time (rebuilding and
  // reclassifying the tail for every anchor is quadratic).
  let tails: TailFacts[] | null = null;
  for (let i = 0; i < n; i++) {
    let nameIdx = signoffNameIndex(lines, i);
    let needDroppable = false;
    if (nameIdx === null) {
      if (!isSignoffNameLine(lines[i] as string)) continue;
      nameIdx = i;
      needDroppable = true;
    }
    if (tails === null) tails = tailDebrisTable(lines);
    const [nonblank, allDebris, anyDroppable] = tails[nameIdx + 1] as TailFacts;
    if (!nonblank) continue;
    if (allDebris && (!needDroppable || anyDroppable)) {
      return lines.slice(0, nameIdx + 1);
    }
  }
  return [...lines];
}

/** `[has a non-blank line, every non-blank line is debris, some non-blank line is droppable]`. */
type TailFacts = readonly [nonblank: boolean, allDebris: boolean, anyDroppable: boolean];

/**
 * For every `p` (`0 <= p <= lines.length`), facts about the non-blank lines of
 * `lines[p:]`: whether there is one, whether every one is debris (droppable,
 * link-only or labeled-link), and whether one is droppable.
 */
function tailDebrisTable(lines: readonly string[]): TailFacts[] {
  const table: TailFacts[] = new Array<TailFacts>(lines.length + 1).fill([false, true, false]);
  for (let p = lines.length - 1; p >= 0; p--) {
    const line = lines[p] as string;
    const [nonblank, allDebris, anyDroppable] = table[p + 1] as TailFacts;
    if (!isBlank(line)) {
      const droppable = isDroppableSignatureLine(line);
      const debris = droppable || LINK_ONLY_LINE_RE.test(line) || LABELED_LINK_LINE_RE.test(line);
      table[p] = [true, allDebris && debris, anyDroppable || droppable];
    } else {
      table[p] = [nonblank, allDebris, anyDroppable];
    }
  }
  return table;
}

// --- greeting / closing sign-off ----------------------------------------------

const GREETING_WORDS =
  // English
  String.raw`hi|hello|hey|dear|greetings|good[ \t]+(?:morning|afternoon|evening|day)` +
  // German
  String.raw`|hallo|guten[ \t]+(?:tag|morgen|abend)|liebe[rs]?|sehr[ \t]+geehrte[rs]?` +
  // French / Spanish / Italian / Portuguese
  String.raw`|bonjour|salut|ch(?:è|e)re?s?|hola|estimad[oa]s?|buenos[ \t]+d(?:í|i)as|ciao|ol(?:á|a)` +
  // Dutch / Nordic
  String.raw`|beste|geachte|hej|hoi` +
  // Māori (common in NZ English mail)
  String.raw`|kia[ \t]+ora|t(?:ē|e)n(?:ā|a)[ \t]+koe`;

// The bare form's tail is `(?:[ \t]*[,!.])?[ \t]*$`, which accepts the same
// strings as the original `[ \t]*[,!.]?[ \t]*$`: written that way, two adjacent
// `[ \t]*` runs split a long blank run in quadratically many ways before a
// non-blank character fails the line.
const GREETING_RE = new PyPattern(
  String.raw`^[ \t]*(?:${GREETING_WORDS})\b(?:[^.!?;:]{0,60}[,!.]|(?:[ \t]*[,!.])?)[ \t]*$`,
  {
    ignoreCase: true,
  },
);

/** Drop an opening greeting line if the first non-blank line is one. */
export function stripGreetingLines(lines: readonly string[]): string[] {
  const idx = lines.findIndex((line) => !isBlank(line));
  if (idx >= 0 && GREETING_RE.test(lines[idx] as string)) {
    return [...lines.slice(0, idx), ...lines.slice(idx + 1)];
  }
  return [...lines];
}

/** Drop a closing sign-off — salutation(+name) — from the end of the text. */
export function stripClosingSignoff(lines: readonly string[]): string[] {
  const nonblank: number[] = [];
  lines.forEach((line, i) => {
    if (!isBlank(line)) nonblank.push(i);
  });
  if (nonblank.length === 0) return [...lines];
  const last = nonblank[nonblank.length - 1] as number;
  for (const i of nonblank.slice(-2)) {
    if (signoffNameIndex(lines, i) === last) return lines.slice(0, i);
  }
  if (SIGNOFF_LINE_RE.test(lines[last] as string)) return lines.slice(0, last);
  return [...lines];
}

// --- confidentiality / legal disclaimers ----------------------------------------

const DISCLAIMER_START_RE = new PyPattern(
  String.raw`^[ \t]*(?:` +
    String.raw`(?:CONFIDENTIALITY|LEGAL|PRIVACY)[ \t]+(?:NOTICE|NOTE|DISCLAIMER|STATEMENT)` +
    String.raw`|DISCLAIMER[ \t]*[:\-]` +
    String.raw`|This[ \t]+(?:e-?mail|message|communication)\b[^.]{0,120}?` +
    String.raw`\b(?:confidential|privileged|intended[ \t]+(?:only|solely)[ \t]+for)` +
    String.raw`|The[ \t]+information[ \t]+(?:contained|transmitted)\b[^.]{0,80}?` +
    String.raw`\b(?:confidential|privileged)` +
    String.raw`|If[ \t]+you[ \t]+(?:are[ \t]+not[ \t]+the[ \t]+intended[ \t]+recipient` +
    String.raw`|(?:have[ \t]+)?received[ \t]+this[ \t]+(?:e-?mail|message)[ \t]+in[ \t]+error)` +
    String.raw`)`,
  { ignoreCase: true },
);

// --- mobile / client taglines -------------------------------------------------

const MOBILE_TAGLINE_RE = new PyPattern(
  String.raw`^[ \t]*(?:` +
    String.raw`Sent from my \w+` +
    String.raw`|Sent from Proton Mail` +
    String.raw`|Sent from Mail for Windows` +
    String.raw`|Sent from Outlook` +
    String.raw`|Get Outlook for \w+` +
    String.raw`)`,
  { ignoreCase: true },
);

/** True for a mobile/webmail client tagline ("Sent from my iPhone"). */
export function isMobileTagline(line: string): boolean {
  return MOBILE_TAGLINE_RE.test(line);
}

// --- pass steps over (original-index, text) pairs -----------------------------

type Pair = readonly [number, string];

function truncateAtSignatureDelimiter(pairs: readonly Pair[]): Pair[] {
  const pos = pairs.findIndex(([, line]) => isSignatureDelimiter(line));
  return pos >= 0 ? pairs.slice(0, pos) : [...pairs];
}

function truncateAtCustomSignatureDivider(pairs: readonly Pair[]): Pair[] {
  for (let pos = 0; pos < pairs.length; pos++) {
    const line = (pairs[pos] as Pair)[1];
    if (!CUSTOM_SIG_DIVIDER_RE.test(line)) continue;
    if (pos > 0 && !isBlank((pairs[pos - 1] as Pair)[1])) continue; // heading underline
    // The first non-blank line after the divider, found by stepping over the
    // blank run (not by slicing the rest of the list, which made every divider
    // cost the length of the text). Divider lines are non-blank, so the runs
    // stepped over for different dividers do not overlap.
    let nxtPos = pos + 1;
    while (nxtPos < pairs.length && isBlank((pairs[nxtPos] as Pair)[1])) nxtPos++;
    if (nxtPos < pairs.length && isSignoffNameLike((pairs[nxtPos] as Pair)[1])) return pairs.slice(0, pos);
  }
  return [...pairs];
}

function stripTrailingDebrisPairs(pairs: readonly Pair[]): Pair[] {
  const kept = stripTrailingSignoffDebris(pairs.map(([, line]) => line));
  return pairs.slice(0, kept.length);
}

function dropSignatureDebris(pairs: readonly Pair[]): Pair[] {
  return pairs.filter(([, line]) => !isDroppableSignatureLine(line));
}

function stripGreetingPairs(pairs: readonly Pair[]): Pair[] {
  const pos = pairs.findIndex(([, line]) => !isBlank(line));
  if (pos >= 0 && GREETING_RE.test((pairs[pos] as Pair)[1])) {
    return [...pairs.slice(0, pos), ...pairs.slice(pos + 1)];
  }
  return [...pairs];
}

function stripClosingSignoffPairs(pairs: readonly Pair[]): Pair[] {
  const kept = stripClosingSignoff(pairs.map(([, line]) => line));
  return pairs.slice(0, kept.length);
}

function dropMobileTaglines(pairs: readonly Pair[]): Pair[] {
  return pairs.filter(([, line]) => !isMobileTagline(line));
}

/** A line that carries a URL, used with the HTML-signature-hint substance guard. */
const URL_RE = new PyPattern(String.raw`https?://|\bwww\.`, { ignoreCase: true });

/** True for a body line the HTML signature container marks as furniture. */
function isHtmlSignatureLine(line: string, hintNorms: ReadonlySet<string>): boolean {
  const norm = normalizeLineForDiff(line);
  if (!norm || !hintNorms.has(norm)) return false;
  return pyWordCount(norm) >= 2 || hasPyDigit(norm) || norm.includes("@") || URL_RE.search(norm) !== null;
}

function dropHtmlSignatureLines(pairs: readonly Pair[], hintNorms: ReadonlySet<string>): Pair[] {
  if (hintNorms.size === 0) return [...pairs];
  return pairs.filter(([, line]) => !isHtmlSignatureLine(line, hintNorms));
}

/** Drop legal/confidentiality boilerplate paragraphs from the tail. */
function dropTrailingDisclaimerParagraphs(pairs: readonly Pair[]): Pair[] {
  const paragraphs: [number, number][] = [];
  let start: number | null = null;
  pairs.forEach(([, line], pos) => {
    if (!isBlank(line)) {
      if (start === null) start = pos;
    } else if (start !== null) {
      paragraphs.push([start, pos]);
      start = null;
    }
  });
  if (start !== null) paragraphs.push([start, pairs.length]);

  let cut: number | null = null;
  for (let p = paragraphs.length - 1; p >= 0; p--) {
    const pStart = (paragraphs[p] as [number, number])[0];
    if (DISCLAIMER_START_RE.test((pairs[pStart] as Pair)[1])) {
      cut = pStart;
      continue;
    }
    break;
  }
  return cut === null ? [...pairs] : pairs.slice(0, cut);
}

/** One full pass of every removal step, in order. */
function cleanPass(pairs: readonly Pair[], hintNorms: ReadonlySet<string>): Pair[] {
  let out = truncateAtSignatureDelimiter(pairs);
  out = truncateAtCustomSignatureDivider(out);
  out = stripTrailingDebrisPairs(out);
  out = dropSignatureDebris(out);
  out = dropHtmlSignatureLines(out, hintNorms);
  out = stripGreetingPairs(out);
  out = dropMobileTaglines(out);
  out = dropTrailingDisclaimerParagraphs(out);
  out = stripClosingSignoffPairs(out);
  return out;
}

/** Fixpoint cap for {@link cleanForScoring}. */
const MAX_CLEAN_PASSES = 4;

/**
 * Strip formulaic furniture from stage-1 `extractedText` for scoring (stage 2).
 *
 * Pure and never throws. Returns the cleaned `text` and the 0-based indices of
 * every non-blank input line removed. `options.htmlSignatureText` (the HTML
 * signature container's visible text) adds a per-line drop hint.
 */
export function cleanForScoring(extractedText: string, options: CleanOptions = {}): CleanResult {
  const hintNorms = new Set<string>();
  for (const line of (options.htmlSignatureText || "").split("\n")) {
    const norm = normalizeLineForDiff(line);
    if (norm) hintNorms.add(norm);
  }

  const original = extractedText.split("\n");
  let pairs: Pair[] = original.map((line, i) => [i, line] as const);

  for (let pass = 0; pass < MAX_CLEAN_PASSES; pass++) {
    const before = pairs.length;
    pairs = cleanPass(pairs, hintNorms);
    if (pairs.length === before) break;
  }

  const keptIndices = new Set(pairs.map(([idx]) => idx));
  const ignoredLines: number[] = [];
  original.forEach((line, i) => {
    if (!isBlank(line) && !keptIndices.has(i)) ignoredLines.push(i);
  });

  const text = collapseAndTrim(pairs.map(([, line]) => pyRstrip(line)));
  return Object.freeze({ text, ignoredLines: Object.freeze(ignoredLines) });
}
