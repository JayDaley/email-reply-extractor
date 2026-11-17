/**
 * Opening greetings for stage 2: the word lists and the per-line decision.
 *
 * Port of `_greetings.py`; its module docstring states the rules. Lengths
 * count code points; `str.lower`, `str.upper`, `str.isalpha`, `str.isalnum`
 * and `str.isupper` follow Python through the table-driven helpers of
 * `pyCompat.ts`.
 *
 * @internal
 */

import { fold, isAlphaString, type Names, reSplit } from "./names.js";
import {
  cpLen,
  firstIsUpper,
  isPyAlnum,
  isPyAlpha,
  isUpperInvariant,
  PyPattern,
  pyLower,
  pyLstrip,
  pyRstrip,
  pyRstripChars,
  pySplit,
  pyStrip,
  pyStripChars,
} from "./pyCompat.js";

// --- word lists ------------------------------------------------------------------------

/** Greeting words, grouped by language; the base list (Python regex source). */
export const GREETING_WORDS =
  // English
  "hi|hello|hey|dear|greetings|good[ \\t]+(?:morning|afternoon|evening|day)" +
  // German
  "|hallo|guten[ \\t]+(?:tag|morgen|abend)|liebe[rs]?|sehr[ \\t]+geehrte[rs]?" +
  // French / Spanish / Italian / Portuguese
  "|bonjour|salut|ch(?:\u00e8|e)re?s?|hola|estimad[oa]s?|buenos[ \\t]+d(?:\u00ed|i)as|ciao|ol(?:\u00e1|a)" +
  // Dutch / Nordic
  "|beste|geachte|hej|hoi" +
  // Māori (common in NZ English mail)
  "|kia[ \\t]+ora|t(?:\u0113|e)n(?:\u0101|a)[ \\t]+koe";
/** Further opening words, accepted only by the rules after the base rule (Python regex source). */
export const EXTRA_GREETING_WORDS =
  "hiya|howdy|moin|re-|good[ \\t]+(?:morning|afternoon|evening|day)[ \\t]+(?:all|everyone|folks)";

/**
 * The base greeting line: a greeting word, then either a short run of names
 * ending in "," / "!" / "." or nothing else.
 */
export const GREETING_RE = new PyPattern(
  `^[ \\t]*(?:${GREETING_WORDS})\\b(?:[^.!?;:]{0,60}[,!.]|(?:[ \\t]*[,!.])?)[ \\t]*$`,
  { ignoreCase: true },
);

export const MAX_GREETING_LEN = 120;

export const GREETING_PREFIX_RE = new PyPattern(
  `^(?:${GREETING_WORDS}|${EXTRA_GREETING_WORDS})(?![\\w])[ \\t]*,?[ \\t]*`,
  { ignoreCase: true },
);
export const THANKS_PREFIX_RE = new PyPattern(
  "^(?:many[ \\t]+thanks|thanks[ \\t]+(?:a[ \\t]+lot|again)|thanks|thank[ \\t]+you)(?![\\w])[ \\t]*,?[ \\t]*",
  { ignoreCase: true },
);
export const ITEM_SPLIT_RE = new PyPattern("[ \\t]*(?:,|/|&|\\+|;)[ \\t]*|[ \\t]+(?:and|or)[ \\t]+", {
  ignoreCase: true,
});
export const ADDRESS_RE = new PyPattern("^<?[\\w.+=-]+@[\\w-]+(?:\\.[\\w-]+)+>?$");
export const SIGNOFF_ABBREVIATION_RE = new PyPattern("[A-Za-z]/[A-Za-z]");
export const CONTRACTION_RE = new PyPattern("(?:n['\u2019]t|['\u2019](?:s|m|re|ve|ll|d))$");

export function foldedSet(words: string): ReadonlySet<string> {
  return new Set(pySplit(words).map(fold));
}

export const TITLES = foldedSet("dr dr. prof prof. professor mr mr. ms ms. mrs mrs. mx sir madam doctor");
/** Group addressees ("all", "chairs", "WG"). */
export const GROUP_WORDS = foldedSet(
  "all everyone everybody folks colleagues team chairs chair cochairs co-chairs authors author" +
    " author(s) co-authors coauthors contributors wg wgs rg working group members iana iesg iab" +
    " irsg ads reviewers editors experts shepherd community people friends gentlemen ladies",
);
/** Words that are name-like only after a greeting word ("Hi there,"). */
export const FILLER_WORDS = foldedSet("there again both guys y'all you");
/** First words that make a short capitalized line a discourse marker or a heading. */
export const HEADING_STOP_WORDS = foldedSet(
  "thanks thank thx regards best cheers sincerely hi hello dear yes no ok okay agreed great" +
    " note notes update hmm well so also fyi inline re regarding sorry first second third finally" +
    " like minor major correction corrections idea ideas off per management done ack template" +
    " specifically question questions addition additionally perhaps maybe sure excellent old new" +
    " right actually errata reminder see target ps p.s btw imo psa pr tl proposed proposal thoughts" +
    " indeed personally instead apparently exactly correct interesting noted approved agree just" +
    " considering summary comment comments issue issues review evaluation subject replace below" +
    " above attention however anyway meanwhile otherwise similarly likewise basically honestly" +
    " frankly oh ah wow cool fine nice good he she they it this that we i you suggestion" +
    " suggestions aside current text section background example examples answer response reply" +
    " nit nits typo support strawman disclaimer ref almost to from as if fwiw iirc afaik afaict" +
    " imho brilliant fantastic awesome wonderful perfect splendid absolutely definitely hooray" +
    " congratulations congrats welcome bingo bravo oops whoops ouch yay yikes wfm lgtm nack ptal" +
    " iiuc afair br ta tx tks tnx rgds kr viele gr\u00fc\u00dfe gruesse gru\u00df mfg vg lg salutations",
);
/** Words that turn a greeting line into a sentence (compared lowercased, with `.,!?:;()*` stripped from the edges). */
export const SENTENCE_WORDS: ReadonlySet<string> = new Set(
  pySplit(
    "i we my our this that these those it is are was were be have has had do does did can could" +
      " would should thanks thank apologies sorry please nice good great just see let here glad" +
      " welcome for some feedback an with about as if not no yes",
  ),
);
export const GENERIC_GROUP_WORDS: readonly string[] = ["group", "members", "people", "community"];

/** A word class of `classify`; `null` is Python's `None`. */
export type Kind = "title" | "name" | "prefix" | "init" | "group" | "filler" | "email" | "acro" | "cap" | null;

export const NAME_KINDS: ReadonlySet<Kind> = new Set<Kind>(["name", "prefix", "title", "init", "group"]);
export const NAME_LIKE_KINDS: ReadonlySet<Kind> = new Set<Kind>([
  "name",
  "prefix",
  "title",
  "init",
  "group",
  "filler",
  "email",
  "acro",
  "cap",
]);

// --- classification ----------------------------------------------------------------------

export function isSentence(words: readonly string[]): boolean {
  for (const word of words) {
    const folded = pyStripChars(pyLower(word), ".,!?:;()*");
    if (SENTENCE_WORDS.has(folded)) return true;
    if (CONTRACTION_RE.search(folded) !== null && !FILLER_WORDS.has(folded)) return true;
  }
  return false;
}

/** The body and the terminator (`"-"`, `","`, `":"`, `"!"`, `"."` or `""`). */
export function splitTerminator(line: string): [string, string] {
  const s = pyRstrip(line);
  if (s.endsWith("-") || s.endsWith("\u2014") || s.endsWith("\u2013")) {
    return [pyRstrip(pyRstripChars(s, "-\u2014\u2013")), "-"];
  }
  const last = s.charAt(s.length - 1);
  if (s && ",:!.".includes(last)) return [pyRstrip(s.slice(0, -1)), last];
  return [s, ""];
}

export function isCapWord(word: string): boolean {
  if (!word || !firstIsUpper(word) || word === "I") return false;
  for (const ch of word) {
    if (!isPyAlpha(ch.codePointAt(0) as number) && !".-'\u2019".includes(ch)) return false;
  }
  return true;
}

function isAlnumString(s: string): boolean {
  if (s.length === 0) return false;
  for (const ch of s) {
    if (!isPyAlnum(ch.codePointAt(0) as number)) return false;
  }
  return true;
}

function hasAlpha(s: string): boolean {
  for (const ch of s) {
    if (isPyAlpha(ch.codePointAt(0) as number)) return true;
  }
  return false;
}

export function classify(words: readonly string[], addressees: Names | null): Kind[] {
  const kinds: Kind[] = [];
  for (const word of words) {
    const folded = pyRstripChars(fold(word), ".");
    const n = cpLen(folded);
    if (TITLES.has(folded) || TITLES.has(`${folded}.`)) kinds.push("title");
    else if (addressees?.tokens.has(folded)) kinds.push("name");
    else if (addressees !== null && n >= 3 && isAlphaString(folded) && addressees.hasPrefix(folded))
      kinds.push("prefix");
    else if (n === 1 && isAlphaString(folded)) kinds.push("init");
    else if (GROUP_WORDS.has(folded)) kinds.push("group");
    else if (FILLER_WORDS.has(folded)) kinds.push("filler");
    else if (ADDRESS_RE.test(word)) kinds.push("email");
    else if (cpLen(word) >= 2 && cpLen(word) <= 12 && isAlnumString(word) && isUpperInvariant(word) && hasAlpha(word))
      kinds.push("acro");
    else if (isCapWord(word)) kinds.push("cap");
    else kinds.push(null);
  }
  return kinds;
}

/** True when `line`, the first non-blank line, is a greeting (see the `_greetings.py` docstring). */
export function isOpeningGreeting(line: string, nextLine: string | null, addressees: Names | null): boolean {
  const s = pyStrip(line);
  if (!s || cpLen(s) > MAX_GREETING_LEN || "+>|*#[(".includes(s.charAt(0))) return false;
  const [body, terminator] = splitTerminator(s);
  const greeting = GREETING_PREFIX_RE.match(body);
  const thanks = greeting ? null : THANKS_PREFIX_RE.match(body);
  const opening = greeting ?? thanks;
  const rest = opening ? body.slice(opening.index + opening[0].length) : body;
  const words = reSplit(ITEM_SPLIT_RE, rest).flatMap((item) => pySplit(item));
  const kinds = classify(words, addressees);
  const n = words.length;

  if (GREETING_RE.test(line)) return !isSentence(pySplit(rest));

  if (addressees !== null && n > 0 && n <= 6 && kinds.every((k) => NAME_KINDS.has(k))) {
    if (kinds.some((k) => k === "name" || k === "prefix")) {
      const thanksOnly = thanks !== null && terminator !== "," && terminator !== ":";
      const barePrefix =
        !opening && (terminator === "." || terminator === "!" || terminator === "") && kinds.includes("prefix");
      if (!thanksOnly && !barePrefix) return true;
    }
  }

  if (greeting !== null) {
    if (n === 0) return true;
    if (n <= 6 && kinds.every((k) => NAME_LIKE_KINDS.has(k)) && kinds.some((k) => k !== "acro")) return true;
  }
  if (
    !opening &&
    (terminator === "," || terminator === ":" || terminator === "-" || terminator === "!") &&
    n > 0 &&
    n <= 6 &&
    kinds.every((k) => k === "group" || k === "acro" || k === "email") &&
    kinds.some((k) => k === "group" || k === "email")
  ) {
    const loneGenericGroup =
      kinds.filter((k) => k === "group").length === 1 &&
      !kinds.includes("acro") &&
      GENERIC_GROUP_WORDS.includes(fold(words[kinds.indexOf("group")] as string));
    if (!loneGenericGroup) return true;
  }

  if (!opening && (terminator === "," || terminator === ":" || terminator === "-") && n > 0 && n <= 4) {
    const items = reSplit(ITEM_SPLIT_RE, rest).filter((item) => pyStrip(item));
    if (terminator !== "," && items.length < 2) return false;
    const trimmed = pyStrip(rest);
    const abbrev = SIGNOFF_ABBREVIATION_RE.match(trimmed);
    if (abbrev !== null && abbrev[0].length === trimmed.length) return false;
    if (
      words.every((w) => isCapWord(w) && !(cpLen(w) >= 7 && pyLower(w).endsWith("ly"))) &&
      !words.some((w) => HEADING_STOP_WORDS.has(pyRstripChars(fold(w), ".")))
    ) {
      return nextLine === null || !pyStrip(nextLine) || firstIsUpper(pyLstrip(nextLine));
    }
  }
  return false;
}
