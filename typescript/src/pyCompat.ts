/**
 * Python semantics the reference implementation relies on: the `str.isspace()`
 * whitespace set and the string methods built on it, `str.isdigit()` and
 * `str.isupper()`, code-point lengths, and a translator from Python `re`
 * pattern syntax to JavaScript regular expressions that keeps `re`'s Unicode
 * `\s \w \d \b`, `.`, `$` and `re.IGNORECASE` semantics (see docs/ports.md,
 * "Semantic hazards").
 *
 * @internal
 */

import {
  DIGIT_CLASS,
  IGNORECASE_EQUIVALENTS,
  ISDIGIT_RANGES,
  ISUPPER_RANGES,
  LETTER_CLASS,
  SPACE_CLASS,
  WORD_CLASS,
} from "./unicodeTables.js";

// --- whitespace ------------------------------------------------------------------

/** The `str.isspace()` set. Every member is in the BMP. */
const SPACE_CODES: ReadonlySet<number> = (() => {
  const set = new Set<number>();
  const re = new RegExp(`[${SPACE_CLASS}]`, "u");
  for (let cp = 0; cp <= 0xffff; cp++) {
    if (re.test(String.fromCharCode(cp))) set.add(cp);
  }
  return set;
})();

/** `ch.isspace()` for one UTF-16 code unit. */
export function isSpaceCode(code: number): boolean {
  return SPACE_CODES.has(code);
}

/** `s.strip()`. */
export function pyStrip(s: string): string {
  let a = 0;
  let b = s.length;
  while (a < b && SPACE_CODES.has(s.charCodeAt(a))) a++;
  while (b > a && SPACE_CODES.has(s.charCodeAt(b - 1))) b--;
  return a === 0 && b === s.length ? s : s.slice(a, b);
}

/** `s.rstrip()`. */
export function pyRstrip(s: string): string {
  let b = s.length;
  while (b > 0 && SPACE_CODES.has(s.charCodeAt(b - 1))) b--;
  return b === s.length ? s : s.slice(0, b);
}

/** `not s.strip()`: true for an empty or whitespace-only string. */
export function isBlank(s: string): boolean {
  for (let i = 0; i < s.length; i++) {
    if (!SPACE_CODES.has(s.charCodeAt(i))) return false;
  }
  return true;
}

/** `s.split()` with no argument. */
export function pySplit(s: string): string[] {
  const out: string[] = [];
  let start = -1;
  for (let i = 0; i < s.length; i++) {
    if (SPACE_CODES.has(s.charCodeAt(i))) {
      if (start >= 0) {
        out.push(s.slice(start, i));
        start = -1;
      }
    } else if (start < 0) {
      start = i;
    }
  }
  if (start >= 0) out.push(s.slice(start));
  return out;
}

/** `len(s.split())`. */
export function pyWordCount(s: string): number {
  let count = 0;
  let inWord = false;
  for (let i = 0; i < s.length; i++) {
    if (SPACE_CODES.has(s.charCodeAt(i))) {
      inWord = false;
    } else if (!inWord) {
      inWord = true;
      count++;
    }
  }
  return count;
}

/** `s.rstrip(chars)` for a set of BMP characters. */
export function pyRstripChars(s: string, chars: string): string {
  let b = s.length;
  while (b > 0 && chars.includes(s.charAt(b - 1))) b--;
  return s.slice(0, b);
}

// --- code points -----------------------------------------------------------------

/** `len(s)`: the number of code points. */
export function cpLen(s: string): number {
  let n = s.length;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) {
        n--;
        i++;
      }
    }
  }
  return n;
}

function inRanges(cp: number, ranges: readonly (readonly [number, number])[]): boolean {
  let lo = 0;
  let hi = ranges.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const r = ranges[mid] as readonly [number, number];
    if (cp < r[0]) hi = mid - 1;
    else if (cp > r[1]) lo = mid + 1;
    else return true;
  }
  return false;
}

/** `any(ch.isdigit() for ch in s)`. */
export function hasPyDigit(s: string): boolean {
  for (const ch of s) {
    if (inRanges(ch.codePointAt(0) as number, ISDIGIT_RANGES)) return true;
  }
  return false;
}

/** `s[0].isupper()` for a non-empty `s`. */
export function firstIsUpper(s: string): boolean {
  const cp = s.codePointAt(0);
  return cp !== undefined && inRanges(cp, ISUPPER_RANGES);
}

// --- regular expressions -------------------------------------------------------------

const WORD = `[${WORD_CLASS}]`;
const BOUNDARY = `(?:(?<=${WORD})(?!${WORD})|(?<!${WORD})(?=${WORD}))`;
const SYNTAX_CHARS = "^$\\.*+?()[]{}|/";

function hex(cp: number): string {
  return `\\u{${cp.toString(16)}}`;
}

function isCased(ch: string): boolean {
  return ch.toLowerCase() !== ch || ch.toUpperCase() !== ch;
}

/** Class-body text for the `re.IGNORECASE` equivalents of one code point. */
function caseEquivalents(cp: number): string {
  const ch = String.fromCodePoint(cp);
  const eq = IGNORECASE_EQUIVALENTS[ch];
  if (eq) return eq.map(hex).join("");
  if (isCased(ch)) {
    throw new Error(`no IGNORECASE table entry for U+${cp.toString(16)}`);
  }
  return hex(cp);
}

/** A pass-through escape (`\n`, `\uXXXX`, `\.` ...) at `src[i]`; returns [text, code point or -1, length]. */
function simpleEscape(src: string, i: number): [string, number, number] {
  const e = src.charAt(i + 1);
  switch (e) {
    case "n":
      return ["\\n", 0x0a, 2];
    case "t":
      return ["\\t", 0x09, 2];
    case "r":
      return ["\\r", 0x0d, 2];
    case "f":
      return ["\\f", 0x0c, 2];
    case "v":
      return ["\\v", 0x0b, 2];
    case "u": {
      const h = src.slice(i + 2, i + 6);
      if (!/^[0-9a-fA-F]{4}$/.test(h)) throw new Error(`bad \\u escape in ${src}`);
      return [`\\u${h}`, Number.parseInt(h, 16), 6];
    }
    case "x": {
      const h = src.slice(i + 2, i + 4);
      if (!/^[0-9a-fA-F]{2}$/.test(h)) throw new Error(`bad \\x escape in ${src}`);
      return [`\\x${h}`, Number.parseInt(h, 16), 4];
    }
    default: {
      const cp = src.codePointAt(i + 1);
      if (cp === undefined) throw new Error(`trailing backslash in ${src}`);
      const ch = String.fromCodePoint(cp);
      if (/^[A-Za-z0-9]$/.test(ch)) throw new Error(`unsupported escape \\${ch} in ${src}`);
      return [SYNTAX_CHARS.includes(ch) ? `\\${ch}` : hex(cp), cp, 1 + ch.length];
    }
  }
}

function translateClass(src: string, start: number, ci: boolean): [string, number] {
  const letter = "[^\\W\\d_]";
  if (src.startsWith(letter, start)) return [`[${LETTER_CLASS}]`, letter.length];
  let i = start + 1;
  let out = "[";
  if (src.charAt(i) === "^") {
    out += "^";
    i++;
  }
  const atom = (): [string, number] => {
    // Returns the item's class text and its code point (-1 for a class escape).
    if (src.charAt(i) === "\\") {
      const e = src.charAt(i + 1);
      if (e === "s" || e === "w" || e === "d") {
        i += 2;
        return [e === "s" ? SPACE_CLASS : e === "w" ? WORD_CLASS : DIGIT_CLASS, -1];
      }
      if (e === "S" || e === "W" || e === "D") throw new Error(`unsupported \\${e} in a class: ${src}`);
      const [text, cp, len] = simpleEscape(src, i);
      i += len;
      return [text, cp];
    }
    const cp = src.codePointAt(i) as number;
    i += cp > 0xffff ? 2 : 1;
    return [hex(cp), cp];
  };
  while (i < src.length && src.charAt(i) !== "]") {
    const [text, lo] = atom();
    if (lo >= 0 && src.charAt(i) === "-" && i + 1 < src.length && src.charAt(i + 1) !== "]") {
      i++;
      const [hiText, hi] = atom();
      if (hi < 0) throw new Error(`bad range in ${src}`);
      out += `${text}-${hiText}`;
      if (ci) {
        for (let cp = lo; cp <= hi; cp++) {
          const ch = String.fromCodePoint(cp);
          if (IGNORECASE_EQUIVALENTS[ch] || isCased(ch)) out += caseEquivalents(cp);
        }
      }
    } else {
      out += ci && lo >= 0 ? caseEquivalents(lo) : text;
    }
  }
  if (src.charAt(i) !== "]") throw new Error(`unterminated class in ${src}`);
  return [`${out}]`, i + 1 - start];
}

/**
 * Translate a Python `re` pattern into JavaScript regex source (for the `u`
 * flag). Supports the syntax the reference uses: classes, the Unicode
 * categories `\s \S \w \W \d \D \b`, `[^\W\d_]`, `.`, `$`, groups including
 * `(?P<name>…)`, lookaround, the scoped flags `(?i:…)` / `(?-i:…)`, and
 * quantifiers. Case-insensitive parts are expanded into explicit classes of
 * `re.IGNORECASE` equivalents, so JavaScript case folding is never used.
 */
export function translatePattern(src: string, ignoreCase = false, dotAll = false): string {
  let out = "";
  let ci = ignoreCase;
  const stack: boolean[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src.charAt(i);
    if (c === "\\") {
      const e = src.charAt(i + 1);
      const cls: Record<string, string> = {
        s: `[${SPACE_CLASS}]`,
        S: `[^${SPACE_CLASS}]`,
        w: WORD,
        W: `[^${WORD_CLASS}]`,
        d: `[${DIGIT_CLASS}]`,
        D: `[^${DIGIT_CLASS}]`,
        b: BOUNDARY,
      };
      const rep = cls[e];
      if (rep !== undefined) {
        out += rep;
        i += 2;
        continue;
      }
      const [text, cp, len] = simpleEscape(src, i);
      out +=
        ci && cp >= 0 && len === 2 && !SYNTAX_CHARS.includes(src.charAt(i + 1)) ? `[${caseEquivalents(cp)}]` : text;
      i += len;
      continue;
    }
    if (c === "[") {
      const [text, len] = translateClass(src, i, ci);
      out += text;
      i += len;
      continue;
    }
    if (c === "(") {
      stack.push(ci);
      if (src.startsWith("(?P<", i)) {
        const end = src.indexOf(">", i);
        out += `(?<${src.slice(i + 4, end)}>`;
        i = end + 1;
      } else if (src.startsWith("(?i:", i)) {
        ci = true;
        out += "(?:";
        i += 4;
      } else if (src.startsWith("(?-i:", i)) {
        ci = false;
        out += "(?:";
        i += 5;
      } else {
        const m = /^\((?:\?(?::|=|!|<=|<!))?/.exec(src.slice(i, i + 4)) as RegExpExecArray;
        if (src.charAt(i + 1) === "?" && m[0].length === 1) throw new Error(`unsupported group in ${src}`);
        out += m[0];
        i += m[0].length;
      }
      continue;
    }
    if (c === ")") {
      ci = stack.pop() as boolean;
      out += ")";
      i++;
      continue;
    }
    if (c === ".") {
      out += dotAll ? "[\\s\\S]" : "[^\\n]";
      i++;
      continue;
    }
    if (c === "$") {
      // Python: end of string, or before a final newline.
      out += "(?=\\n?$)";
      i++;
      continue;
    }
    if (c === "{") {
      const m = /^\{\d*(?:,\d*)?\}/.exec(src.slice(i));
      if (!m) throw new Error(`literal { in ${src}`);
      out += m[0];
      i += m[0].length;
      continue;
    }
    if ("^*+?|".includes(c)) {
      out += c;
      i++;
      continue;
    }
    const cp = src.codePointAt(i) as number;
    out += ci ? `[${caseEquivalents(cp)}]` : SYNTAX_CHARS.includes(c) ? `\\${c}` : hex(cp);
    i += cp > 0xffff ? 2 : 1;
  }
  if (stack.length !== 0) throw new Error(`unbalanced group in ${src}`);
  return out;
}

export interface PyFlags {
  /** `re.IGNORECASE`. */
  readonly ignoreCase?: boolean;
  /** `re.DOTALL`. */
  readonly dotAll?: boolean;
}

/** A compiled Python pattern with `re`'s `match`, `search` and `sub`. */
export class PyPattern {
  readonly source: string;
  private readonly sticky: RegExp;
  private readonly global: RegExp;

  constructor(pattern: string, flags: PyFlags = {}) {
    this.source = translatePattern(pattern, flags.ignoreCase ?? false, flags.dotAll ?? false);
    this.sticky = new RegExp(this.source, "uy");
    this.global = new RegExp(this.source, "ug");
  }

  /** `pattern.match(s, pos)`: anchored at `pos` (a UTF-16 index). */
  match(s: string, pos = 0): RegExpExecArray | null {
    this.sticky.lastIndex = pos;
    return this.sticky.exec(s);
  }

  /** `bool(pattern.match(s))`. */
  test(s: string): boolean {
    this.sticky.lastIndex = 0;
    return this.sticky.test(s);
  }

  /** `pattern.search(s, pos)`. */
  search(s: string, pos = 0): RegExpExecArray | null {
    this.global.lastIndex = pos;
    return this.global.exec(s);
  }

  /** `pattern.sub(fn, s)` with a function replacement. */
  sub(s: string, fn: (m: RegExpExecArray) => string): string {
    let out = "";
    let last = 0;
    this.global.lastIndex = 0;
    for (;;) {
      const m = this.global.exec(s);
      if (m === null) break;
      out += s.slice(last, m.index) + fn(m);
      last = m.index + m[0].length;
      if (m[0].length === 0) {
        // Python never re-matches an empty match at the same position.
        if (last >= s.length) break;
        const cp = s.codePointAt(last) as number;
        out += String.fromCodePoint(cp);
        last += cp > 0xffff ? 2 : 1;
        this.global.lastIndex = last;
      }
    }
    return out + s.slice(last);
  }
}

/** Escape `s` for use as a literal inside JavaScript regex source (`u` flag). */
export function escapeRegex(s: string): string {
  return s.replace(/[\\^$.*+?()[\]{}|/]/g, "\\$&");
}

/** Word-character class source (`re`'s Unicode `\w`), for hand-built patterns. */
export const WORD_RE_CLASS = WORD;
