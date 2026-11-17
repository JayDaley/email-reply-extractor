/**
 * Display names for stage 2: folding, tokens, and lines that name a person.
 *
 * Port of `_names.py`; its module docstring states the rules. `fold` is
 * Python's `str.lower` (`pyLower`, table-driven) followed by one pass through
 * the literal `FOLD_TABLE` copied from the Python source. Lengths, indexes
 * that are compared with limits, and first letters count code points; token
 * lists are sorted by code point, as Python's `sorted`.
 *
 * @internal
 */

import { FOLD_TABLE } from "./foldTable.js";
import {
  compareCodePoints,
  cpLen,
  firstIsUpper,
  isPyAlpha,
  PyPattern,
  pyLower,
  pyLstripChars,
  pyRstrip,
  pyRstripChars,
  pySplit,
  pyStrip,
  pyStripChars,
} from "./pyCompat.js";

// --- regex helpers -----------------------------------------------------------------

/** `pattern.findall(s)` for a pattern that never matches the empty string. */
export function findAll(pattern: PyPattern, s: string, group = 0): string[] {
  const out: string[] = [];
  let pos = 0;
  for (;;) {
    const m = pattern.search(s, pos);
    if (m === null) break;
    out.push(m[group] ?? "");
    pos = m.index + m[0].length;
  }
  return out;
}

/** `pattern.split(s)` for a pattern without groups that never matches the empty string. */
export function reSplit(pattern: PyPattern, s: string): string[] {
  const out: string[] = [];
  let last = 0;
  let pos = 0;
  for (;;) {
    const m = pattern.search(s, pos);
    if (m === null) break;
    out.push(s.slice(last, m.index));
    last = m.index + m[0].length;
    pos = last;
  }
  out.push(s.slice(last));
  return out;
}

/** The first code point of `s` as a string (`s[0]`, or `""` for an empty `s`). */
function firstChar(s: string): string {
  const cp = s.codePointAt(0);
  return cp === undefined ? "" : String.fromCodePoint(cp);
}

/** `s[::-1]`, by code points. */
function reverse(s: string): string {
  return Array.from(s).reverse().join("");
}

// --- folding -------------------------------------------------------------------------

/** Lowercase `text` and map every character through `FOLD_TABLE` (one pass). */
export function fold(text: string): string {
  const lower = pyLower(text);
  let out = "";
  for (const ch of lower) {
    const rep = FOLD_TABLE.get(ch);
    out += rep === undefined ? ch : rep;
  }
  return out;
}

// --- parsing -------------------------------------------------------------------------

/**
 * Words that are never name tokens: titles, generation suffixes, connectors,
 * and the words of list and role display names.
 */
export const STOP_WORDS: ReadonlySet<string> = new Set(
  pySplit(
    "dr prof mr mrs ms mx sir dipl ing phd jr sr ii iii iv the of and via team" +
      " on behalf for list mailing ietf iesg iab irtf secretariat",
  ),
);

/** Local parts of an address that name a mailbox, not a person. */
export const GENERIC_LOCAL_PARTS: ReadonlySet<string> = new Set(
  pySplit("mail email me info contact hello ietf post office"),
);

/** Common English short forms of given names. */
export const NICKNAMES: ReadonlyMap<string, readonly string[]> = new Map([
  ["robert", ["bob", "rob", "bobby", "robby"]],
  ["thomas", ["tom", "tommy"]],
  ["susan", ["sue", "susie"]],
  ["william", ["bill", "will", "billy"]],
  ["richard", ["dick", "rich", "rick"]],
  ["james", ["jim", "jimmy", "jamie"]],
  ["michael", ["mike", "mick"]],
  ["andrew", ["andy", "drew"]],
  ["edward", ["ed", "ted", "eddie"]],
  ["elizabeth", ["liz", "beth", "betsy"]],
  ["margaret", ["peggy", "maggie", "meg"]],
  ["charles", ["chuck", "charlie"]],
  ["joseph", ["joe"]],
  ["jonathan", ["jon"]],
  ["anthony", ["tony"]],
  ["david", ["dave"]],
  ["stephen", ["steve"]],
  ["steven", ["steve"]],
  ["matthew", ["matt"]],
  ["benjamin", ["ben"]],
  ["nicholas", ["nick"]],
  ["patrick", ["pat"]],
  ["peter", ["pete"]],
  ["samuel", ["sam"]],
  ["alexander", ["alex"]],
  ["kenneth", ["ken"]],
  ["donald", ["don"]],
  ["ronald", ["ron"]],
  ["lawrence", ["larry"]],
  ["gregory", ["greg"]],
  ["timothy", ["tim"]],
  ["jeffrey", ["jeff"]],
  ["frederick", ["fred"]],
  ["theodore", ["ted"]],
  ["katherine", ["kate", "kathy"]],
  ["catherine", ["kate", "cathy"]],
  ["deborah", ["deb"]],
  ["rebecca", ["becky"]],
  ["jennifer", ["jen", "jenny"]],
  ["victoria", ["vicky"]],
  ["christopher", ["chris"]],
  ["christine", ["chris"]],
  ["daniel", ["dan", "danny"]],
  ["philip", ["phil"]],
  ["phillip", ["phil"]],
  ["raymond", ["ray"]],
  ["gerald", ["gerry"]],
  ["leonard", ["len"]],
]);

export const LETTER_RUN_RE = new PyPattern("[^\\W\\d_]+");
export const JOINED_RUN_RE = new PyPattern("[^\\W\\d_]+(?:['\u2019-][^\\W\\d_]+)*");
export const ADDRESS_PUNCT_RE = new PyPattern("[._+=-]");
export const NICKNAME_GROUP_RE = new PyPattern("\\(([^()]{1,30})\\)");
export const BRACKETED_RE = new PyPattern("\\([^()]*\\)|\\[[^\\[\\]]*\\]|<[^<>]*>");
export const WORD_SPAN_RE = new PyPattern("\\S+");

/** Binary search: the first index of `sorted` whose string is not below `text`. */
function bisectLeft(sorted: readonly string[], text: string): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (compareCodePoints(sorted[mid] as string, text) < 0) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** The tokens of one or more display names. `initials` and `firstLast` are set only for a single name. */
export class Names {
  readonly tokens: ReadonlySet<string>;
  readonly initials: ReadonlySet<string>;
  readonly firstLast: string;
  /** The tokens sorted by code point. */
  readonly sorted: readonly string[];
  /** The reversed tokens sorted by code point. */
  readonly sortedReversed: readonly string[];

  constructor(
    tokens: ReadonlySet<string>,
    initials: ReadonlySet<string> = new Set(),
    firstLast = "",
    sorted: readonly string[] = [],
    sortedReversed: readonly string[] = [],
  ) {
    this.tokens = tokens;
    this.initials = initials;
    this.firstLast = firstLast;
    this.sorted = sorted;
    this.sortedReversed = sortedReversed;
  }

  /** True when `text` is a prefix of some token. */
  hasPrefix(text: string): boolean {
    const pos = bisectLeft(this.sorted, text);
    return pos < this.sorted.length && (this.sorted[pos] as string).startsWith(text);
  }

  /** True when `text` is a suffix of some token. */
  hasSuffix(text: string): boolean {
    const rev = reverse(text);
    const pos = bisectLeft(this.sortedReversed, rev);
    return pos < this.sortedReversed.length && (this.sortedReversed[pos] as string).startsWith(rev);
  }
}

/** `text` up to the first word `via` with whitespace on both sides. */
export function dropVia(text: string): string {
  let pos = 0;
  for (;;) {
    const m = WORD_SPAN_RE.search(text, pos);
    if (m === null) return text;
    const end = m.index + m[0].length;
    if (m.index > 0 && end < text.length && pyLower(m[0]) === "via") {
      return pyRstrip(text.slice(0, m.index));
    }
    pos = end;
  }
}

/** `local` with `.`, `_`, `+`, `=`, `-` read as spaces. */
function addressWords(local: string): string {
  return ADDRESS_PUNCT_RE.sub(local, () => " ");
}

/** `s.isalpha()`: non-empty and every code point alphabetic. */
export function isAlphaString(s: string): boolean {
  if (s.length === 0) return false;
  for (const ch of s) {
    if (!isPyAlpha(ch.codePointAt(0) as number)) return false;
  }
  return true;
}

/** Steps 1-5 of the `_names.py` docstring: the name text and the extra texts. */
export function reduce(name: string): [string, string[]] {
  let text = pyStripChars(pyStrip(name.replaceAll('"', " ").replaceAll("`", " ")), "'");
  const extra: string[] = [];
  if (text.includes("@") && !text.includes(" ")) {
    const at = text.indexOf("@");
    const local = text.slice(0, at);
    const domain = text.slice(at + 1);
    const label = domain.split(".")[0] as string;
    if (GENERIC_LOCAL_PARTS.has(fold(local)) && isAlphaString(label) && cpLen(label) >= 6) {
      extra.push(label);
    }
    text = addressWords(local);
  }
  extra.push(...findAll(NICKNAME_GROUP_RE, text, 1));
  text = BRACKETED_RE.sub(text, () => " ");
  if (text.includes("@")) {
    text = pySplit(text)
      .map((word) => (word.includes("@") ? addressWords(word.slice(0, word.indexOf("@"))) : word))
      .join(" ");
  }
  text = dropVia(text);
  const commas = text.split(",");
  if (commas.length === 2) {
    text = `${commas[1]} ${commas[0]}`;
  }
  return [text, extra];
}

/** The token set of `texts` (Python's `_tokens`), with nicknames added. */
export function tokens(texts: Iterable<string>): Set<string> {
  const out = new Set<string>();
  for (const text of texts) {
    const folded = fold(text);
    for (const run of [...findAll(LETTER_RUN_RE, folded), ...findAll(JOINED_RUN_RE, folded)]) {
      if (cpLen(run) >= 2 && !STOP_WORDS.has(run)) out.add(run);
    }
  }
  for (const token of [...out]) {
    for (const nick of NICKNAMES.get(token) ?? []) out.add(nick);
  }
  return out;
}

/** The tokens of one display name. */
export function nameTokens(name: string): ReadonlySet<string> {
  const [text, extra] = reduce(name);
  return tokens([text, ...extra]);
}

/** A `Names` value with its sorted token lists. */
export function make(tokenSet: Iterable<string>, initials: ReadonlySet<string> = new Set(), firstLast = ""): Names {
  const set = new Set(tokenSet);
  return new Names(
    set,
    initials,
    firstLast,
    [...set].sort(compareCodePoints),
    [...set].map(reverse).sort(compareCodePoints),
  );
}

/** The sender's `Names`, with initials, or `null` without tokens. */
export function parseAuthorName(name: string | null | undefined): Names | null {
  if (!name) return null;
  const [text, extra] = reduce(name);
  const toks = tokens([text, ...extra]);
  if (toks.size === 0) return null;
  const words = findAll(LETTER_RUN_RE, fold(text)).filter((w) => !STOP_WORDS.has(w));
  const initials = new Set<string>();
  let firstLast = "";
  if (words.length >= 2) {
    for (const seq of [words, [...words].reverse()]) {
      initials.add(seq.map(firstChar).join(""));
      initials.add(firstChar(seq[0] as string) + firstChar(seq[seq.length - 1] as string));
    }
    firstLast = firstChar(words[0] as string) + firstChar(words[words.length - 1] as string);
  }
  return make(toks, new Set([...initials].filter((i) => cpLen(i) >= 2)), firstLast);
}

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

/**
 * The addressees' `Names`, or `null` without tokens. A name whose tokens
 * equal the sender's own (`authorName`) is skipped.
 */
export function parseAddresseeNames(names: Iterable<string>, authorName: string | null = null): Names | null {
  const own: ReadonlySet<string> = authorName ? nameTokens(authorName) : new Set();
  const out = new Set<string>();
  for (const name of names) {
    const theirs = nameTokens(name);
    if (own.size === 0 || !sameSet(theirs, own)) {
      for (const t of theirs) out.add(t);
    }
  }
  return out.size > 0 ? make(out) : null;
}

// --- name lines ------------------------------------------------------------------------

/** Name particles: allowed in a name line when they are tokens of the name, never the word that matches it. */
export const NAME_PARTICLES: ReadonlySet<string> = new Set(
  pySplit("de da di del della der den van von la le du dos das bin al el"),
);

/** Characters that decorate a sign-off name ("-Jane", "— Jane", "~jane", "/J"). */
export const NAME_DECORATION = "-\u2013\u2014~/*_ \t";

/** A role or proxy tail after a name ("Jane on behalf of the chairs", "Jane, for the authors"). */
export const NAME_LINE_TAIL_RE = new PyPattern(
  "(?:(?<![ \\t])[ \\t]+(?:on[ \\t]+behalf[ \\t]+of|for[ \\t]+the)\\b.{0,60}" +
    "|(?<![ \\t])[ \\t]*(?://|,)[ \\t]*(?:as|for|on|speaking|with|wearing|no|contributor|individual)\\b.{0,40})$",
  { ignoreCase: true },
);
/** One word of a name line after folding. */
export const FOLDED_NAME_WORD_RE = new PyPattern("^[^\\W\\d_](?:[^\\W\\d_]|[.'\u2019\\-])*$");
/** A capitalized word that is not the sender's (a co-signer, a middle name). */
export const LOOSE_NAME_TOKEN_RE = new PyPattern("^[^\\W\\d_][\\w'\u2019\\-]{0,24}$");
/** Words that join co-signers in a group sign-off ("Jane and the co-authors"). */
export const GROUP_SIGNOFF_WORDS: ReadonlySet<string> = new Set(
  pySplit("and & co-authors coauthors authors chairs co-chairs editors team"),
);

/** 2: `word` matches the sender; 1: an initial or particle; 0: neither. */
export function nameWordStatus(word: string, author: Names): number {
  const folded = pyStripChars(fold(word), ".");
  if (!folded || !FOLDED_NAME_WORD_RE.test(folded)) return 0;
  if (folded.includes("-")) {
    const parts = folded.split("-").filter((part) => part);
    return parts.length > 0 && parts.every((p) => nameWordStatus(p, author) === 2) ? 2 : 0;
  }
  const core = folded.replaceAll(".", "").replaceAll("'", "").replaceAll("\u2019", "");
  if (!core) return 0;
  if (NAME_PARTICLES.has(core)) return author.tokens.has(core) ? 1 : 0;
  if (author.tokens.has(core) || author.initials.has(core)) return 2;
  const n = cpLen(core);
  if (n >= 3 && (author.hasPrefix(core) || author.hasSuffix(core))) return 2;
  if (n === 3 && author.firstLast) {
    const cps = Array.from(core);
    if ((cps[0] as string) + (cps[2] as string) === author.firstLast) return 2;
  }
  if (n === 1) return 1;
  return 0;
}

/** True when `word` matches the sender's name (status 2 above). */
export function matchesAuthor(word: string, author: Names): boolean {
  return nameWordStatus(word, author) === 2;
}

/** True when a folded letter run of `word` is an addressee token. */
export function matchesAddressee(word: string, addressees: Names | null): boolean {
  if (addressees === null) return false;
  return findAll(LETTER_RUN_RE, fold(word)).some((run) => addressees.tokens.has(run));
}

const NAME_LINE_TRAILING = " \t.,;:!";

/** True when `line` is the sender's own name (see `is_author_name_line` in `_names.py`). */
export function isAuthorNameLine(line: string, author: Names | null): boolean {
  if (author === null) return false;
  let s = pyStrip(line);
  const close = s.indexOf(")");
  // 0 < s.find(")") <= 30, in code points.
  if (s.startsWith("(") && close > 0 && cpLen(s.slice(0, close)) <= 30) {
    s = s.slice(close + 1);
  }
  s = pyLstripChars(s, NAME_DECORATION);
  s = NAME_LINE_TAIL_RE.sub(s, () => "");
  s = pyRstripChars(s, NAME_LINE_TRAILING);
  const openParen = s.lastIndexOf("(");
  if (s.endsWith(")") && openParen > 0 && cpLen(s.slice(openParen)) <= 40) {
    s = pyRstripChars(s.slice(0, openParen), NAME_LINE_TRAILING);
  }
  if (!s || cpLen(s) > 40) return false;
  const words = pySplit(s);
  if (words.length > 4) return false;
  const group = words.some((w) => {
    const low = pyLower(w);
    return low === "and" || low === "&";
  });
  const statuses: number[] = [];
  for (const word of words) {
    if (group && GROUP_SIGNOFF_WORDS.has(pyLower(word))) {
      statuses.push(1);
      continue;
    }
    const status = nameWordStatus(word, author);
    if (status === 0 && !(firstIsUpper(word) && LOOSE_NAME_TOKEN_RE.test(word))) return false;
    statuses.push(status);
  }
  const unknown = statuses.filter((x) => x === 0).length;
  const matched = statuses.filter((x) => x === 2).length;
  if (!matched) return false;
  return unknown === 0 || (unknown === 1 && (group || matched >= 2));
}

/** A decorated one-letter sign-off ("/J", "-J.") that is the sender's first initial. */
export function isAuthorInitialLine(line: string, author: Names | null): boolean {
  if (author === null) return false;
  const s = pyStrip(line);
  const core = pyRstripChars(pyLstripChars(s, NAME_DECORATION), " .");
  if (cpLen(core) !== 1 || !isAlphaString(core) || core === s) return false;
  const folded = fold(core);
  for (const initials of author.initials) {
    if (firstChar(initials) === folded) return true;
  }
  return false;
}
