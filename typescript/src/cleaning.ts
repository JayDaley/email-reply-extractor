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
  SIGNOFF_SALUTATIONS,
  signoffNameIndex,
} from "./extraction.js";
import { isOpeningGreeting } from "./greetings.js";
import {
  isAlphaString,
  isAuthorInitialLine,
  isAuthorNameLine,
  LOOSE_NAME_TOKEN_RE,
  matchesAddressee,
  matchesAuthor,
  NAME_DECORATION,
  type Names,
  parseAddresseeNames,
  parseAuthorName,
  reSplit,
} from "./names.js";
import {
  codePointAt,
  compareCodePoints,
  cpLen,
  firstIsUpper,
  hasPyDigit,
  isBlank,
  isPyAlpha,
  isPyLower,
  isSpaceCode,
  PyPattern,
  pyLower,
  pyLstripChars,
  pyRstrip,
  pyRstripChars,
  pySplit,
  pyStrip,
  pyStripChars,
  pyWordCount,
} from "./pyCompat.js";

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
  /**
   * `text` divided at the stage-1 part boundaries passed as
   * `options.partStarts` (see `ExtractionResult.partStarts`). Each part holds
   * the surviving lines of one stage-1 part, formatted as `text` is; a part
   * that cleaning emptied is omitted. Without `partStarts` it is `[text]`, or
   * `[]` when `text` is empty. The non-blank lines of the parts, in order, are
   * exactly those of `text`.
   */
  readonly parts: readonly string[];
}

/** Options of {@link cleanForScoring}. */
export interface CleanOptions {
  /** The visible text of the message's HTML signature container (`splitHtmlParts(html).signatureText`). */
  readonly htmlSignatureText?: string | null;
  /**
   * The stage-1 result's `partStarts`. Indices outside `1 .. lines - 1` are
   * ignored, and the rest are used in ascending order without duplicates. It
   * changes only `parts`, never `text` or `ignoredLines`.
   */
  readonly partStarts?: readonly number[] | null;
  /**
   * The display name of the message's sender (the From header's name, or its
   * address when there is no name). It enables the sender-name sign-off rules
   * and the thanks-to-an-addressee exception.
   */
  readonly authorName?: string | null;
  /**
   * The display names of the people the message answers or is addressed to
   * (the parent message's sender, the To and Cc names). They enable the
   * addressee greeting rule and the thanks-to-an-addressee exception.
   */
  readonly addresseeNames?: readonly string[] | null;
  /** The sender's learned signature lines, dropped after the passes converge. */
  readonly signatureLines?: readonly string[] | null;
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

// --- salutations ------------------------------------------------------------------

// Valedictions beyond the list stage 1 shares (extraction's SIGNOFF_SALUTATIONS):
// the lines found directly above a sender-name line in mailing-list mail.
const EXTRA_SALUTATIONS =
  String.raw`with\s+(?:best|kind|warm(?:est)?)\s+regards|with\s+thanks|all\s+best|` +
  String.raw`thanks\s+(?:a\s+lot|in\s+advance|very\s+much|all|folks|everyone)|thankyou|` +
  String.raw`thank\s+you\s+(?:very\s+much|again|all)|many\s+thx|` +
  String.raw`take\s+care|so\s+long|see\s+you\s+soon|warmly|later|greetings|ave|` +
  String.raw`r[’']s|b\.?\s?r\.?|brgds|thanx|` +
  String.raw`(?:a|à)\s+bient(?:ô|o)t|cordialmente`;
const ONE_SALUTATION = String.raw`(?:${EXTRA_SALUTATIONS}|${SIGNOFF_SALUTATIONS})`;
// Two salutations joined: "Thanks and regards", "Regards/Ngā mihi", "Thanks, Cheers".
const SALUTATION = String.raw`${ONE_SALUTATION}(?:[ \t]*(?:[/,&+]|and)[ \t]*${ONE_SALUTATION})?`;
// Punctuation after a salutation: "," "，" "、" ":" a run of "!", one to three "." or none.
const SALUTATION_PUNCT = String.raw`(?:[,:，、]|!+|\.{1,3})`;
/** A line that is only a salutation, with the tolerant list and punctuation. */
const SALUTATION_LINE_RE = new PyPattern(String.raw`^[ \t]*${SALUTATION}[ \t]*(?:${SALUTATION_PUNCT}[ \t]*)?$`, {
  ignoreCase: true,
});
/** The one-line form: salutation, punctuation, name ("Later, Jane", "Regards，Jane"). */
const SALUTATION_INLINE_RE = new PyPattern(
  String.raw`^[ \t]*${SALUTATION}[ \t]*(?P<punct>${SALUTATION_PUNCT})[ \t]*(?P<name>\S.*)$`,
  { ignoreCase: true },
);
/** The one-line form with a decorated name and no punctuation ("Thanks -Jane"). */
const SALUTATION_DECORATED_RE = new PyPattern(String.raw`^[ \t]*${SALUTATION}[ \t]+(?P<name>[-–—~/]+[ \t]*\S.*)$`, {
  ignoreCase: true,
});
/** Any one-line form: optional punctuation, then the rest of the line. */
const SALUTATION_ANY_INLINE_RE = new PyPattern(
  String.raw`^[ \t]*${SALUTATION}[ \t]*(?:[,:，!.][ \t]*)?(?P<name>\S.*)$`,
  { ignoreCase: true },
);
/** The one-line form with only blanks after the salutation ("Thanks Jane"). */
const SALUTATION_SPACE_INLINE_RE = new PyPattern(String.raw`^[ \t]*${SALUTATION}[ \t]+(?P<name>\S.*)$`, {
  ignoreCase: true,
});
const INLINE_COMMAS: ReadonlySet<string> = new Set([",", "，", "、"]);

/** The named group `name` of a match (always present when the pattern matched). */
function nameGroup(m: RegExpExecArray): string {
  return m.groups?.name as string;
}

function stripNameDecoration(text: string): [string, boolean] {
  const stripped = pyStrip(text);
  const core = pyLstripChars(stripped, NAME_DECORATION);
  return [pyRstripChars(core, " \t.,;:!"), core !== stripped];
}

/**
 * A sign-off name in the tolerant forms: a name line whose first four words
 * are capitalized, when decorated ("— Jane Doe") or when `allowPlain`; or a
 * single loose name word that is decorated ("-jane"), indented, lower-case
 * ("jane"), or any word when `allowPlain`.
 */
function isLooseSignoffName(text: string, allowPlain: boolean): boolean {
  const [core, decorated] = stripNameDecoration(text);
  if (!core) return false;
  const words = pySplit(core);
  if ((decorated || allowPlain) && isSignoffNameLike(core)) {
    if (words.slice(0, 4).every(firstIsUpper)) return true;
  }
  const first = text.charAt(0);
  const indented = first === " " || first === "\t";
  if (words.length === 1 && LOOSE_NAME_TOKEN_RE.test(core)) {
    return decorated || indented || isPyLower(codePointAt(core, 0)) || allowPlain;
  }
  return false;
}

/**
 * {@link signoffNameIndex} with the tolerant forms: after the shared forms, a
 * salutation line whose next non-blank line is a loose name (plain names
 * allowed); the one-line form with punctuation whose name is a decorated or
 * lower-case loose name, or, after a comma, a name line; the decorated
 * one-line form.
 */
function tolerantSignoffNameIndex(lines: readonly string[], i: number): number | null {
  const shared = signoffNameIndex(lines, i);
  if (shared !== null) return shared;
  const line = lines[i] as string;
  if (SALUTATION_LINE_RE.test(line)) {
    let j = i + 1;
    while (j < lines.length && isBlank(lines[j] as string)) j++;
    if (j < lines.length && isLooseSignoffName(lines[j] as string, true)) return j;
    return null;
  }
  const inline = SALUTATION_INLINE_RE.match(line);
  if (inline) {
    const name = nameGroup(inline);
    if (isLooseSignoffName(name, false)) return i;
    if (INLINE_COMMAS.has(inline.groups?.punct as string) && isSignoffNameLike(pyStrip(name))) return i;
    return null;
  }
  const decorated = SALUTATION_DECORATED_RE.match(line);
  if (decorated && isLooseSignoffName(nameGroup(decorated), false)) return i;
  return null;
}

// --- thanks to an addressee -------------------------------------------------------

const THANKS_SALUTATION_RE = new PyPattern(
  String.raw`^[ \t]*(?:many[ \t]+|with[ \t]+)?(?:thanks|thank[ \t]*you|thx|thanx)\b`,
  {
    ignoreCase: true,
  },
);
// A sentence break after a word of 2+ letters (not a title) and more text.
const FURTHER_WORDS_RE = new PyPattern(
  String.raw`(?<![\w.])(?!(?:dr|mr|mrs|ms|mx|prof|jr|sr|st)[.])[^\W\d_]{2,}[.!?;:][ \t]+\S`,
  { ignoreCase: true },
);
const NAME_LIST_SPLIT_RE = new PyPattern(String.raw`[\s,/&+]+`);

/**
 * True when the one-line thanks form `line` thanks a participant: it opens
 * with a thanks salutation and whitespace precedes the name, and a sentence
 * follows the name, or no word of the name matches the sender and one matches
 * an addressee.
 */
function isThanksToAddressee(line: string, author: Names, addressees: Names | null): boolean {
  if (!THANKS_SALUTATION_RE.test(line)) return false;
  const inline = SALUTATION_ANY_INLINE_RE.match(line);
  if (!inline) return false;
  const rawName = nameGroup(inline);
  // The name runs to the end of the line, so it starts at length - name length.
  const nameStart = line.length - rawName.length;
  if (nameStart === 0 || !isSpaceCode(line.charCodeAt(nameStart - 1))) return false;
  const name = pyLstripChars(rawName, " ,，");
  if (FURTHER_WORDS_RE.search(name)) return true;
  const words = reSplit(NAME_LIST_SPLIT_RE, pyStripChars(name, " \t.,;:!-–—~*_")).filter((w) => w !== "");
  if (words.some((word) => matchesAuthor(word, author))) return false;
  return words.some((word) => matchesAddressee(word, addressees));
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

/** Drop up to two opening greeting lines, within the text's greeting budget. */
function stripOpeningGreetings(pairs: Pair[], state: CleanState): Pair[] {
  for (let n = 0; n < MAX_GREETINGS; n++) {
    if (state.greetingsLeft === 0) break;
    const pos = pairs.findIndex(([, line]) => !isBlank(line));
    if (pos < 0) break;
    const nextLine = pos + 1 < pairs.length ? (pairs[pos + 1] as Pair)[1] : null;
    if (!isOpeningGreeting((pairs[pos] as Pair)[1], nextLine, state.addressees)) break;
    pairs = [...pairs.slice(0, pos), ...pairs.slice(pos + 1)];
    state.greetingsLeft -= 1;
  }
  return pairs;
}

const PUNCT_BEFORE_BLANK_RE = new PyPattern(String.raw`[.,;:!?][ \t]`);

/**
 * Drop a closing sign-off from the end of the text: on the last two non-blank
 * lines, a shared sign-off whose name is the last line, then a tolerant one,
 * each truncating at its first line; then a last line that is a salutation
 * alone. A one-line thanks form that thanks a participant is kept (only with a
 * sender name). A one-line form on a line a sender-name rule exposed is removed
 * only when its name is a plain name.
 */
function stripClosingSignoff(pairs: Pair[], state: CleanState): Pair[] {
  const lines = pairs.map(([, line]) => line);
  const nonblank: number[] = [];
  lines.forEach((line, i) => {
    if (!isBlank(line)) nonblank.push(i);
  });
  if (nonblank.length === 0) return pairs;
  const last = nonblank[nonblank.length - 1] as number;
  const lastLine = lines[last] as string;
  if (state.exposed.has((pairs[last] as Pair)[0])) {
    const inline = SALUTATION_ANY_INLINE_RE.match(lastLine);
    if (inline && !SIGNOFF_LINE_RE.test(lastLine)) {
      const name = pyRstripChars(pyLstripChars(nameGroup(inline), " ,，"), " .!");
      const plain =
        isSignoffNameLine(name) && PUNCT_BEFORE_BLANK_RE.search(name) === null && pySplit(name).every(firstIsUpper);
      if (!plain) return pairs;
    }
  }
  for (const findName of [signoffNameIndex, tolerantSignoffNameIndex]) {
    for (const i of nonblank.slice(-2)) {
      if (findName(lines, i) === last) {
        if (
          state.author !== null &&
          i === last &&
          isThanksToAddressee(lines[i] as string, state.author, state.addressees)
        ) {
          return pairs;
        }
        return pairs.slice(0, i);
      }
    }
  }
  if (SALUTATION_LINE_RE.test(lastLine)) return pairs.slice(0, last);
  return pairs;
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

// --- sign-offs with the sender's name ---------------------------------------------

/** A rule line drawn directly above a signature name ("—", "---", "___"). */
const ABOVE_NAME_DIVIDER_RE = new PyPattern(String.raw`^[ \t]*[-–—_=~*]+[ \t]*$`);
/** A footnote or reference line ("[1] https://…"). */
const FOOTNOTE_RE = new PyPattern(String.raw`^[ \t]*\[\d{1,3}\]`);
/** A postscript ("PS", "P.S.", "p.p.s."). */
const POSTSCRIPT_RE = new PyPattern(String.raw`^[ \t]*p\.?[ \t]?(?:p\.?[ \t]?)?s\b`, { ignoreCase: true });
const SENTENCE_END_RE = new PyPattern(String.raw`[.?!:;]\s*$`);
/** Lower-case words a signature line may hold besides capitalized ones. */
const SIGNATURE_CONNECTORS: ReadonlySet<string> = new Set(
  "of and for the at de in & und et y du von van der la le".split(" "),
);
/** A rule line of three or more `-_=*~#`. */
const RULE_LINE_RE = new PyPattern(String.raw`^[ \t]*[-_=*~#]{3,}[ \t]*$`);
const LIST_BULLET_RE = new PyPattern(String.raw`^[ \t]*(?:[*•+]|\d+[.)])[ \t]`);

/**
 * A line of a signature block under the sender's name: signature debris, a
 * link-only or "Label: URL" line; or a line of at most 8 words, not ending in
 * sentence punctuation, that holds a digit, `@` or a URL, or whose
 * letter-initial words, connectors aside, are mostly capitalized. Never a
 * footnote line.
 */
function isAttachedSignatureLine(line: string): boolean {
  const s = pyStrip(line);
  if (!s || FOOTNOTE_RE.test(s)) return false;
  if (LINK_ONLY_LINE_RE.test(s) || LABELED_LINK_LINE_RE.test(s) || isDroppableSignatureLine(s)) return true;
  const words = pySplit(s);
  if (words.length > 8 || SENTENCE_END_RE.search(s) !== null) return false;
  if (hasPyDigit(s) || s.includes("@") || URL_RE.search(s) !== null) return true;
  const alpha = words.filter((w) => isPyAlpha(codePointAt(w, 0)) && !SIGNATURE_CONNECTORS.has(pyLower(w)));
  if (alpha.length === 0) return false;
  return alpha.filter(firstIsUpper).length * 2 > alpha.length;
}

/**
 * A line that may follow a sign-off without making it content: blank, a
 * footnote, a link-only or "Label: URL" line, signature debris, a client
 * tagline, or a rule line.
 */
function isNonblockingTailLine(line: string): boolean {
  const s = pyStrip(line);
  return (
    !s ||
    FOOTNOTE_RE.test(s) ||
    LINK_ONLY_LINE_RE.test(s) ||
    LABELED_LINK_LINE_RE.test(s) ||
    isDroppableSignatureLine(s) ||
    isMobileTagline(s) ||
    CUSTOM_SIG_DIVIDER_RE.test(s) ||
    RULE_LINE_RE.test(s)
  );
}

/**
 * The lines kept from the non-blocking tail below a sign-off: blank lines and
 * footnotes, link lines when the tail holds a footnote, and a postscript with
 * everything after it.
 */
function keptTail(tail: readonly Pair[]): Pair[] {
  const keepLinks = tail.some(([, line]) => FOOTNOTE_RE.test(line));
  const kept: Pair[] = [];
  for (let pos = 0; pos < tail.length; pos++) {
    const pair = tail[pos] as Pair;
    const line = pair[1];
    if (POSTSCRIPT_RE.test(line)) return [...kept, ...tail.slice(pos)];
    const s = pyStrip(line);
    if (s && !FOOTNOTE_RE.test(s)) {
      const isLink = LINK_ONLY_LINE_RE.test(s) || LABELED_LINK_LINE_RE.test(s);
      if (!(isLink && keepLinks)) continue;
    }
    kept.push(pair);
  }
  return kept;
}

/** The position of the last non-blank line above `pos`, or -1. */
function previousNonblank(pairs: readonly Pair[], pos: number): number {
  let k = pos - 1;
  while (k >= 0 && isBlank((pairs[k] as Pair)[1])) k--;
  return k;
}

/** Record the non-blank line above `pos`: a removal exposes it as the tail. */
function markExposed(pairs: readonly Pair[], pos: number, state: CleanState): void {
  const k = previousNonblank(pairs, pos);
  if (k >= 0) state.exposed.add((pairs[k] as Pair)[0]);
}

/** `pos`, or the position of a rule line at most one blank line above it. */
function dividerAbove(pairs: readonly Pair[], pos: number): number {
  const k = previousNonblank(pairs, pos);
  if (k >= 0 && pos - k <= 2 && ABOVE_NAME_DIVIDER_RE.test((pairs[k] as Pair)[1])) return k;
  return pos;
}

/**
 * Drop the first sender-name line followed only by signature and tail lines:
 * the name line, the attached signature lines directly below it, and the
 * salutation line above it (or else a rule line directly above the name), when
 * everything below is non-blocking up to the end or up to a postscript. Of that
 * tail, {@link keptTail} decides what stays. A name line that is the last
 * non-blank line on its own is left to {@link dropTrailingAuthorName}.
 */
function dropSignoffAboveTail(pairs: Pair[], state: CleanState): Pair[] {
  const n = pairs.length;
  // nonblocking[p]: every line of pairs[p:] is non-blocking, or a postscript comes first.
  const nonblocking = new Array<boolean>(n + 1).fill(true);
  const blankFrom = new Array<boolean>(n + 1).fill(true);
  // attachedEnd[p]: the end of the run of attached signature lines at p.
  const attachedEnd = Array.from({ length: n + 1 }, (_, p) => p);
  for (let p = n - 1; p >= 0; p--) {
    const line = (pairs[p] as Pair)[1];
    nonblocking[p] = POSTSCRIPT_RE.test(line) || (isNonblockingTailLine(line) && (nonblocking[p + 1] as boolean));
    blankFrom[p] = (blankFrom[p + 1] as boolean) && isBlank(line);
    if (isAttachedSignatureLine(line)) attachedEnd[p] = attachedEnd[p + 1] as number;
  }
  for (let i = 0; i < n; i++) {
    if (!isAuthorNameLine((pairs[i] as Pair)[1], state.author)) continue;
    const j = attachedEnd[i + 1] as number;
    if (!nonblocking[j] || (j === i + 1 && blankFrom[j])) continue;
    const k = previousNonblank(pairs, i);
    const start = k >= 0 && SALUTATION_LINE_RE.test((pairs[k] as Pair)[1]) ? k : dividerAbove(pairs, i);
    markExposed(pairs, start, state);
    return [...pairs.slice(0, start), ...keptTail(pairs.slice(j))];
  }
  return pairs;
}

/**
 * Drop a last non-blank line that is the sender's name or decorated initial,
 * with a rule line directly above it.
 */
function dropTrailingAuthorName(pairs: Pair[], state: CleanState): Pair[] {
  let pos = pairs.length - 1;
  while (pos >= 0 && isBlank((pairs[pos] as Pair)[1])) pos--;
  if (pos < 0) return pairs;
  const line = (pairs[pos] as Pair)[1];
  if (isAuthorNameLine(line, state.author) || isAuthorInitialLine(line, state.author)) {
    const start = dividerAbove(pairs, pos);
    markExposed(pairs, start, state);
    return pairs.slice(0, start);
  }
  return pairs;
}

/**
 * Drop every salutation + sender-name sign-off, wherever it stands: the
 * two-line form (a salutation line, then the sender's name as the next
 * non-blank line) and the one-line form whose name is the sender's.
 */
function dropAuthorSignoffs(pairs: Pair[], author: Names): Pair[] {
  const n = pairs.length;
  const drop = new Set<number>();
  for (let i = 0; i < n; i++) {
    const line = (pairs[i] as Pair)[1];
    if (isBlank(line)) continue;
    if (SALUTATION_LINE_RE.test(line)) {
      let j = i + 1;
      while (j < n && isBlank((pairs[j] as Pair)[1])) j++;
      if (j < n && isAuthorNameLine((pairs[j] as Pair)[1], author)) {
        drop.add(i);
        drop.add(j);
        continue;
      }
    }
    const inline = SALUTATION_INLINE_RE.match(line) ?? SALUTATION_SPACE_INLINE_RE.match(line);
    if (inline && isAuthorNameLine(nameGroup(inline), author)) drop.add(i);
  }
  if (drop.size === 0) return pairs;
  return pairs.filter((_, p) => !drop.has(p));
}

/**
 * `line` without a final `(…)` of at most 60 characters and the blanks around
 * it, or `null` when it has none.
 */
function withoutTrailingParenthetical(line: string): string | null {
  const s = pyRstripChars(line, " \t");
  if (!s.endsWith(")")) return null;
  const openParen = s.lastIndexOf("(");
  if (openParen < 0) return null;
  const inner = s.slice(openParen + 1, -1);
  if (inner.includes(")") || cpLen(inner) > 60) return null;
  return pyRstripChars(s.slice(0, openParen), " \t");
}

/** A capitalized name line, a trailing parenthesis aside (a list of names). */
function isNameShaped(line: string): boolean {
  const bare = withoutTrailingParenthetical(line);
  const s = pyStrip(bare === null ? line : bare);
  return isSignoffNameLike(s) && pySplit(s).filter(isAlphaString).every(firstIsUpper);
}

/**
 * Drop a sender-name line away from the tail with no salutation above it:
 * every non-blank line but the first and the last, when it is the sender's
 * name, does not end in `:` or `,`, is not a list item, and the non-blank line
 * above is not a salutation, not name-shaped and does not end in `:`. A name
 * line with a trailing parenthesis followed by a name-shaped line is part of a
 * list too.
 */
function dropLoneAuthorName(pairs: Pair[], author: Names): Pair[] {
  const nonblank: number[] = [];
  pairs.forEach(([, line], p) => {
    if (!isBlank(line)) nonblank.push(p);
  });
  const drop = new Set<number>();
  for (let k = 1; k < nonblank.length - 1; k++) {
    const p = nonblank[k] as number;
    const line = (pairs[p] as Pair)[1];
    if (!isAuthorNameLine(line, author)) continue;
    const trimmed = pyRstrip(line);
    if (trimmed.endsWith(":") || trimmed.endsWith(",") || LIST_BULLET_RE.test(line)) continue;
    const above = (pairs[nonblank[k - 1] as number] as Pair)[1];
    if (SALUTATION_LINE_RE.test(above) || isNameShaped(above) || pyRstrip(above).endsWith(":")) continue;
    const below = (pairs[nonblank[k + 1] as number] as Pair)[1];
    if (withoutTrailingParenthetical(line) !== null && isNameShaped(below)) continue;
    drop.add(p);
  }
  if (drop.size === 0) return pairs;
  return pairs.filter((_, p) => !drop.has(p));
}

// --- learned signature lines -------------------------------------------------------

/** {@link learnSignatureLines} thresholds. */
const LEARN_MIN_MESSAGES = 5;
const LEARN_MIN_COUNT = 3;
const LEARN_MIN_SUBJECTS = 3;
const LEARN_TAIL_LINES = 10;
/** A non-learned line in the trailing zone has at most this many words. */
const LEARNED_ZONE_MAX_WORDS = 4;

/**
 * The reply/forward prefixes and bracketed list tags at the start of a
 * subject, any number of them ("Re: [list] AW: Fwd[2]: …").
 */
const SUBJECT_PREFIXES_RE = new PyPattern(
  String.raw`(?:\s*(?:\[[^\]]*\]|(?:re|fw|fwd|aw|sv|antw|wg|tr)(?:\s*\[\d+\])?\s*:)\s*)*`,
  { ignoreCase: true },
);

/** `str.lower` with `ß` (U+00DF) written `ss`. */
function lower(text: string): string {
  return pyLower(text).replaceAll("\u00df", "ss");
}

/**
 * A line as learned lines are compared: normalized for the parent diff
 * (quote markers and whitespace runs removed), then lowercased with `ß`
 * written `ss` ({@link lower}).
 */
function learnedNorm(line: string): string {
  return lower(normalizeLineForDiff(line));
}

function subjectKey(subject: string | null | undefined): string {
  const s = subject ?? "";
  const m = SUBJECT_PREFIXES_RE.match(s);
  const rest = s.slice(m === null ? 0 : m[0].length);
  return lower(pySplit(rest).join(" "));
}

function endsWithSentencePunct(norm: string): boolean {
  return norm.endsWith(".") || norm.endsWith("?") || norm.endsWith("!");
}

/** Options for {@link learnSignatureLines}. */
export interface LearnOptions {
  /** The subjects of the messages, one per text. */
  readonly subjects?: readonly (string | null)[] | null;
}

/**
 * Learn the furniture lines one sender repeats at the end of their messages.
 *
 * `texts` are the stage-1 texts of one sender's messages, and
 * `options.subjects` (optional) their subjects, one per text. Lines are
 * compared in a normalized form: quote markers removed, whitespace runs
 * collapsed to one space, edges stripped, lowercased (`ß` written `ss`). A
 * normalized line is learned when it is among the last 10 non-blank lines of
 * at least 3 of the texts and of at least one fifth of them, and, with
 * subjects, when those texts have at least 3 distinct subjects (compared
 * without leading "Re:"/"Fwd:"-style prefixes and "[list]" tags). Fewer than 5
 * texts learn nothing. A subject that is `null`, `undefined` or empty is the
 * empty subject; a text with no corresponding subject (`subjects` shorter
 * than `texts`) has the empty subject, and subjects beyond the last text are
 * ignored. A candidate that ends in ":" or is a sentence (at least
 * 3 words ending in ".", "?" or "!") is learned only when, in at least half of
 * its occurrences, the line just above is another candidate.
 *
 * Returns the learned lines in their normalized form, sorted by code point;
 * pass them as `signatureLines` to {@link cleanForScoring}. Pure.
 */
export function learnSignatureLines(texts: readonly (string | null)[], options: LearnOptions = {}): string[] {
  if (texts.length < LEARN_MIN_MESSAGES) return [];
  const subjects = options.subjects ?? null;
  const tails: string[][] = [];
  const occurrences = new Map<string, number[]>();
  texts.forEach((text, i) => {
    const norms: string[] = [];
    for (const line of (text || "").split("\n")) {
      const n = learnedNorm(line);
      if (n) norms.push(n);
    }
    const tail = norms.slice(-LEARN_TAIL_LINES);
    tails.push(tail);
    for (const norm of new Set(tail)) {
      const where = occurrences.get(norm);
      if (where === undefined) occurrences.set(norm, [i]);
      else where.push(i);
    }
  });
  const candidates = new Set<string>();
  for (const [norm, where] of occurrences) {
    if (where.length < LEARN_MIN_COUNT || where.length * 5 < texts.length) continue;
    if (subjects !== null) {
      const keys = new Set(where.map((i) => subjectKey(subjects[i])));
      if (keys.size < LEARN_MIN_SUBJECTS) continue;
    }
    candidates.add(norm);
  }
  const learned: string[] = [];
  for (const norm of [...candidates].sort(compareCodePoints)) {
    const opensBlock = norm.endsWith(":") || (endsWithSentencePunct(norm) && pyWordCount(norm) >= 3);
    if (opensBlock) {
      let continued = 0;
      let total = 0;
      for (const i of occurrences.get(norm) as number[]) {
        const tail = tails[i] as string[];
        tail.forEach((line, pos) => {
          if (line !== norm) return;
          total++;
          const above = pos > 0 ? (tail[pos - 1] as string) : null;
          if (above !== null && candidates.has(above) && above !== norm) continued++;
        });
      }
      if (continued * 2 < total) continue;
    }
    learned.push(norm);
  }
  return learned;
}

/**
 * Drop the learned lines in the trailing zone.
 *
 * The trailing zone is scanned from the last line up and ends at the first
 * non-blank line that is not learned and has more than
 * {@link LEARNED_ZONE_MAX_WORDS} words. Every learned line inside it is
 * dropped, unless no non-blank, non-learned line stands above the topmost of
 * them: the step never removes the whole text.
 */
function dropLearnedSignatureLines(pairs: Pair[], learned: ReadonlySet<string>): Pair[] {
  if (learned.size === 0) return pairs;
  const norms = pairs.map(([, line]) => learnedNorm(line));
  const drop = new Set<number>();
  for (let pos = pairs.length - 1; pos >= 0; pos--) {
    const norm = norms[pos] as string;
    if (!norm) continue;
    if (learned.has(norm)) drop.add(pos);
    else if (pyWordCount(norm) > LEARNED_ZONE_MAX_WORDS) break;
  }
  if (drop.size === 0) return pairs;
  let first = pairs.length;
  for (const pos of drop) first = Math.min(first, pos);
  let anyAbove = false;
  for (let p = 0; p < first; p++) {
    const norm = norms[p] as string;
    if (norm && !learned.has(norm)) {
      anyAbove = true;
      break;
    }
  }
  if (!anyAbove) return pairs;
  return pairs.filter((_, p) => !drop.has(p));
}

// --- the pass -----------------------------------------------------------------------

/** Greeting lines removed per pass and per text. */
const MAX_GREETINGS = 2;

/** Evidence and bookkeeping that persist across the passes of one text. */
interface CleanState {
  readonly author: Names | null;
  readonly addressees: Names | null;
  /** Greeting lines that may still be removed (at most {@link MAX_GREETINGS}). */
  greetingsLeft: number;
  /** Input indices of lines that a sender-name rule exposed as the tail. */
  readonly exposed: Set<number>;
}

/** One full pass of every removal step, in order. */
function cleanPass(pairs: Pair[], hintNorms: ReadonlySet<string>, state: CleanState): Pair[] {
  let out = truncateAtSignatureDelimiter(pairs);
  out = truncateAtCustomSignatureDivider(out);
  // Trailing-debris before the per-line debris drop: its bare-name anchor needs
  // the identifier/contact lines still present as evidence.
  out = stripTrailingDebrisPairs(out);
  out = dropSignatureDebris(out);
  out = dropHtmlSignatureLines(out, hintNorms);
  out = stripOpeningGreetings(out, state);
  // Taglines and disclaimers go before the closing sign-off so a sign-off with
  // boilerplate below it is exposed as the true tail and removed in the same pass.
  out = dropMobileTaglines(out);
  out = dropTrailingDisclaimerParagraphs(out);
  out = stripClosingSignoff(out, state);
  if (state.author !== null) {
    // A salutation these rules expose at the tail goes on the next pass.
    out = dropSignoffAboveTail(out, state);
    out = dropTrailingAuthorName(out, state);
    out = dropAuthorSignoffs(out, state.author);
    out = dropLoneAuthorName(out, state.author);
  }
  return out;
}

/** Fixpoint cap for {@link cleanForScoring}; it doubles when learned lines are given. */
const MAX_CLEAN_PASSES = 4;

/**
 * Strip formulaic furniture from stage-1 `extractedText` for scoring (stage 2).
 *
 * Pure and never throws. Returns the cleaned `text` and the 0-based indices of
 * every non-blank input line removed. `options.htmlSignatureText` (the HTML
 * signature container's visible text) adds a per-line drop hint;
 * `options.authorName` and `options.addresseeNames` enable the sender-name and
 * addressee rules. With no evidence only the rules that need none run.
 */
export function cleanForScoring(extractedText: string, options: CleanOptions = {}): CleanResult {
  const hintNorms = new Set<string>();
  for (const line of (options.htmlSignatureText || "").split("\n")) {
    const norm = normalizeLineForDiff(line);
    if (norm) hintNorms.add(norm);
  }

  const learned = new Set<string>();
  for (const line of options.signatureLines ?? []) {
    const norm = learnedNorm(line);
    if (norm) learned.add(norm);
  }
  const state: CleanState = {
    author: parseAuthorName(options.authorName ?? null),
    addressees: parseAddresseeNames(options.addresseeNames ?? [], options.authorName ?? null),
    greetingsLeft: MAX_GREETINGS,
    exposed: new Set<number>(),
  };

  const original = extractedText.split("\n");
  let pairs: Pair[] = original.map((line, i) => [i, line] as const);

  // The learned-line drop runs only once the ordinary passes have converged,
  // so the other steps keep their anchors; the passes then resume, since the
  // drop can expose more furniture.
  const maxPasses = learned.size > 0 ? MAX_CLEAN_PASSES * 2 : MAX_CLEAN_PASSES;
  for (let pass = 0; pass < maxPasses; pass++) {
    const before = pairs.length;
    pairs = cleanPass(pairs, hintNorms, state);
    if (pairs.length !== before) continue;
    pairs = dropLearnedSignatureLines(pairs, learned);
    if (pairs.length === before) break;
  }

  const keptIndices = new Set(pairs.map(([idx]) => idx));
  const ignoredLines: number[] = [];
  original.forEach((line, i) => {
    if (!isBlank(line) && !keptIndices.has(i)) ignoredLines.push(i);
  });

  const text = formatLines(pairs.map(([, line]) => line));

  const starts = [...new Set(options.partStarts ?? [])]
    .filter((s) => Number.isInteger(s) && s > 0 && s < original.length)
    .sort((a, b) => a - b);
  let parts: string[];
  if (!text) {
    parts = [];
  } else if (starts.length === 0) {
    parts = [text];
  } else {
    // Pairs are in ascending index order, so one forward pointer assigns each
    // line to its group (the reference's bisect_right).
    const groups: string[][] = Array.from({ length: starts.length + 1 }, () => []);
    let g = 0;
    for (const [idx, line] of pairs) {
      while (g < starts.length && (starts[g] as number) <= idx) g++;
      (groups[g] as string[]).push(line);
    }
    parts = groups.map(formatLines).filter((part) => part !== "");
  }

  return Object.freeze({ text, ignoredLines: Object.freeze(ignoredLines), parts: Object.freeze(parts) });
}

/**
 * Final formatting of surviving lines: right-strip each line, collapse runs of
 * 2+ blank lines to one, and trim blank edges.
 */
function formatLines(lines: readonly string[]): string {
  return collapseAndTrim(lines.map(pyRstrip));
}
