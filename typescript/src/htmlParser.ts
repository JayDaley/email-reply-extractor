/**
 * A port of the subset of CPython 3.14's `html.parser.HTMLParser` (with
 * `convert_charrefs=True`, fed the whole document then closed) and of
 * `html.unescape` that the reference implementation's `html_text.py` relies on.
 * The tokenizer is reproduced, not a browser's tree construction: see
 * docs/ports.md, "`html.parser.HTMLParser`".
 *
 * Indices are UTF-16 offsets. Every position the tokenizer computes is
 * relative to text it has already scanned, so the offsets agree with
 * CPython's code-point offsets on the same characters; regexes that count
 * characters use the `u` flag.
 *
 * @internal
 */

import { HTML5_ENTITIES } from "./entities.js";
import { pyStrip } from "./pyCompat.js";

// --- html.unescape -------------------------------------------------------------------

const INVALID_CHARREFS: ReadonlyMap<number, string> = new Map([
  [0x00, "\ufffd"],
  [0x0d, "\r"],
  [0x80, "\u20ac"],
  [0x81, "\x81"],
  [0x82, "\u201a"],
  [0x83, "\u0192"],
  [0x84, "\u201e"],
  [0x85, "\u2026"],
  [0x86, "\u2020"],
  [0x87, "\u2021"],
  [0x88, "\u02c6"],
  [0x89, "\u2030"],
  [0x8a, "\u0160"],
  [0x8b, "\u2039"],
  [0x8c, "\u0152"],
  [0x8d, "\x8d"],
  [0x8e, "\u017d"],
  [0x8f, "\x8f"],
  [0x90, "\x90"],
  [0x91, "\u2018"],
  [0x92, "\u2019"],
  [0x93, "\u201c"],
  [0x94, "\u201d"],
  [0x95, "\u2022"],
  [0x96, "\u2013"],
  [0x97, "\u2014"],
  [0x98, "\u02dc"],
  [0x99, "\u2122"],
  [0x9a, "\u0161"],
  [0x9b, "\u203a"],
  [0x9c, "\u0153"],
  [0x9d, "\x9d"],
  [0x9e, "\u017e"],
  [0x9f, "\u0178"],
]);

function isInvalidCodepoint(n: number): boolean {
  return (
    (n >= 0x1 && n <= 0x8) ||
    (n >= 0xe && n <= 0x1f) ||
    (n >= 0x7f && n <= 0x9f) ||
    (n >= 0xfdd0 && n <= 0xfdef) ||
    n === 0xb ||
    (n & 0xfffe) === 0xfffe
  );
}

/** CPython's default `sys.get_int_max_str_digits()`: `int()` of a longer decimal string raises. */
const INT_MAX_STR_DIGITS = 4300;

/** The error `int()` raises for an over-long decimal string (`ValueError`). */
export class IntConversionError extends Error {
  constructor() {
    super("Exceeds the limit (4300 digits) for integer string conversion");
    this.name = "ValueError";
  }
}

function parseIntPy(digits: string, radix: 10 | 16): number {
  if (radix === 10 && digits.length > INT_MAX_STR_DIGITS) throw new IntConversionError();
  // Beyond 0x10FFFF the exact value is irrelevant: it maps to U+FFFD.
  const trimmed = digits.replace(/^0+/, "");
  if (trimmed.length > 8) return Number.MAX_SAFE_INTEGER;
  return trimmed === "" ? 0 : Number.parseInt(trimmed, radix);
}

function replaceCharref(s: string): string {
  if (s.charAt(0) === "#") {
    let num: number;
    if (s.charAt(1) === "x" || s.charAt(1) === "X") {
      num = parseIntPy(s.slice(2).replace(/;+$/, ""), 16);
    } else {
      num = parseIntPy(s.slice(1).replace(/;+$/, ""), 10);
    }
    const mapped = INVALID_CHARREFS.get(num);
    if (mapped !== undefined) return mapped;
    if ((num >= 0xd800 && num <= 0xdfff) || num > 0x10ffff) return "\ufffd";
    if (isInvalidCodepoint(num)) return "";
    return String.fromCodePoint(num);
  }
  const whole = HTML5_ENTITIES.get(s);
  if (whole !== undefined) return whole;
  // The longest matching name (as defined by the standard), by code points.
  const cps = Array.from(s);
  for (let x = cps.length - 1; x > 1; x--) {
    const prefix = cps.slice(0, x).join("");
    const value = HTML5_ENTITIES.get(prefix);
    if (value !== undefined) return value + cps.slice(x).join("");
  }
  return `&${s}`;
}

const CHARREF_RE = /&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)/gu;

/** `html.htmlUnescape(s)`. */
export function htmlUnescape(s: string): string {
  if (!s.includes("&")) return s;
  return s.replace(CHARREF_RE, (_m, ref: string) => replaceCharref(ref));
}

const ATTR_CHARREF_RE = /&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*)[;=]?/gu;

function unescapeAttrValue(s: string): string {
  return s.replace(ATTR_CHARREF_RE, (ref: string) => {
    if (ref.startsWith("&#")) return htmlUnescape(ref);
    if (!ref.endsWith("=") && HTML5_ENTITIES.has(ref.slice(1))) return htmlUnescape(ref);
    return ref;
  });
}

// --- html.parser.HTMLParser --------------------------------------------------------

const STARTTAGOPEN = /<[a-zA-Z]/y;
const ENDTAGOPEN = /<\/[a-zA-Z]/y;
const COMMENTCLOSE = /--!?>/g;
const COMMENTABRUPTCLOSE = /-?>/y;
const TAGFIND_TOLERANT = /([a-zA-Z][^\t\n\r\f />]*)(?:[\t\n\r\f ]|\/(?!>))*/uy;
const ATTRFIND_TOLERANT =
  /((?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*)([\t\n\r\f ]*=[\t\n\r\f ]*('[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?(?:[\t\n\r\f ]|\/(?!>))*/uy;
const LOCATETAGEND =
  /[a-zA-Z][^\t\n\r\f />]*[\t\n\r\f /]*(?:(?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*(?:[\t\n\r\f ]*=[\t\n\r\f ]*(?:'[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?[\t\n\r\f /]*)*>?/uy;

const CDATA_CONTENT_ELEMENTS: ReadonlySet<string> = new Set([
  "script",
  "style",
  "xmp",
  "iframe",
  "noembed",
  "noframes",
]);
const RCDATA_CONTENT_ELEMENTS: ReadonlySet<string> = new Set(["textarea", "title"]);

function at(re: RegExp, s: string, pos: number): RegExpExecArray | null {
  re.lastIndex = pos;
  return re.exec(s);
}

/** An attribute: lowercased name, and value (`null` when the attribute has none). */
export type Attr = readonly [string, string | null];

/**
 * `HTMLParser(convert_charrefs=True)` driven by `feed(html)` then `close()`,
 * with the handlers the reference overrides. Comments, declarations,
 * processing instructions and CDATA sections are discarded (the reference
 * does not override their handlers).
 */
export abstract class HtmlParser {
  private rawdata = "";
  private cdataElem: string | null = null;
  private escapable = true;
  private interesting: RegExp | null = null;

  protected abstract handleStarttag(tag: string, attrs: readonly Attr[]): void;
  protected abstract handleEndtag(tag: string): void;
  protected abstract handleData(data: string): void;

  /** `feed(data)` followed by `close()`. */
  parse(data: string): void {
    this.rawdata = data;
    this.goahead();
  }

  private setCdataMode(elem: string, escapable: boolean): void {
    this.cdataElem = elem.toLowerCase();
    this.escapable = escapable;
    if (this.cdataElem === "plaintext") {
      // `\z`: everything to the end of the document is text (see goahead).
      this.interesting = null;
      return;
    }
    // re.IGNORECASE | re.ASCII: only ASCII letters fold.
    const name = Array.from(this.cdataElem)
      .map((ch) => (/[a-z]/.test(ch) ? `[${ch}${ch.toUpperCase()}]` : ch.replace(/[\\^$.*+?()[\]{}|/]/g, "\\$&")))
      .join("");
    this.interesting = new RegExp(`<\\/${name}(?=[\\t\\n\\r\\f />])`, "gu");
  }

  private clearCdataMode(): void {
    this.interesting = null;
    this.cdataElem = null;
    this.escapable = true;
  }

  /**
   * `goahead(end=True)`. Feeding the whole document and closing is equivalent
   * to a single pass with `end` true: every point where `goahead(0)` stops is
   * re-entered by `close()` at the same index and handled as at end of input.
   */
  private goahead(): void {
    const rawdata = this.rawdata;
    let i = 0;
    const n = rawdata.length;
    while (i < n) {
      let j: number;
      if (this.cdataElem === null) {
        j = rawdata.indexOf("<", i);
        if (j < 0) j = n;
      } else if (this.interesting === null) {
        j = n; // plaintext
      } else {
        const m = at(this.interesting, rawdata, i);
        if (m) {
          j = m.index;
        } else {
          break;
        }
      }
      if (i < j) {
        if (this.escapable) this.handleData(htmlUnescape(rawdata.slice(i, j)));
        else this.handleData(rawdata.slice(i, j));
      }
      i = j;
      if (i === n) break;
      // rawdata[i] is "<" here: in normal mode j is the next "<", and in CDATA
      // mode `interesting` only matches an end tag.
      let k: number;
      if (at(STARTTAGOPEN, rawdata, i)) {
        k = this.parseStarttag(i);
      } else if (rawdata.startsWith("</", i)) {
        k = this.parseEndtag(i);
      } else if (rawdata.startsWith("<!--", i)) {
        k = this.parseComment(i);
      } else if (rawdata.startsWith("<?", i)) {
        k = this.parsePi(i);
      } else if (rawdata.startsWith("<!", i)) {
        k = this.parseHtmlDeclaration(i);
      } else {
        this.handleData("<");
        k = i + 1;
      }
      if (k < 0) {
        if (at(STARTTAGOPEN, rawdata, i)) {
          // incomplete start tag: dropped
        } else if (rawdata.startsWith("</", i)) {
          if (i + 2 === n) this.handleData("</");
          // else: an incomplete end tag or a bogus comment, dropped
        }
        // comments, CDATA, declarations and PIs: dropped
        k = n;
      }
      i = k;
    }
    if (i < n) {
      if (this.escapable) this.handleData(htmlUnescape(rawdata.slice(i, n)));
      else this.handleData(rawdata.slice(i, n));
    }
    this.rawdata = "";
  }

  private parseHtmlDeclaration(i: number): number {
    const rawdata = this.rawdata;
    if (rawdata.startsWith("<!--", i)) return this.parseComment(i);
    if (rawdata.startsWith("<![CDATA[", i)) {
      const j = rawdata.indexOf("]]>", i + 9);
      return j < 0 ? -1 : j + 3;
    }
    if (rawdata.slice(i, i + 9).toLowerCase() === "<!doctype") {
      const gtpos = rawdata.indexOf(">", i + 9);
      return gtpos === -1 ? -1 : gtpos + 1;
    }
    return this.parseBogusComment(i);
  }

  private parseComment(i: number): number {
    const rawdata = this.rawdata;
    COMMENTCLOSE.lastIndex = i + 4;
    let m = COMMENTCLOSE.exec(rawdata);
    if (!m) {
      m = at(COMMENTABRUPTCLOSE, rawdata, i + 4);
      if (!m) return -1;
    }
    return m.index + m[0].length;
  }

  private parseBogusComment(i: number): number {
    const pos = this.rawdata.indexOf(">", i + 2);
    return pos === -1 ? -1 : pos + 1;
  }

  private parsePi(i: number): number {
    const pos = this.rawdata.indexOf(">", i + 2);
    return pos === -1 ? -1 : pos + 1;
  }

  private parseStarttag(i: number): number {
    const rawdata = this.rawdata;
    const endpos = this.checkForWholeStartTag(i);
    if (endpos < 0) return endpos;
    const attrs: Attr[] = [];
    const match = at(TAGFIND_TOLERANT, rawdata, i + 1) as RegExpExecArray;
    let k = match.index + match[0].length;
    const tag = (match[1] as string).toLowerCase();
    while (k < endpos) {
      const m = at(ATTRFIND_TOLERANT, rawdata, k);
      if (!m) break;
      const attrname = m[1] as string;
      const rest = m[2];
      let attrvalue: string | null = m[3] ?? null;
      if (!rest) {
        attrvalue = null;
      } else if (
        attrvalue !== null &&
        attrvalue.length >= 1 &&
        ((attrvalue.startsWith("'") && attrvalue.endsWith("'")) ||
          (attrvalue.startsWith('"') && attrvalue.endsWith('"')))
      ) {
        // Python: attrvalue[:1] == "'" == attrvalue[-1:] (a lone quote qualifies).
        attrvalue = attrvalue.slice(1, -1);
      }
      if (attrvalue) attrvalue = unescapeAttrValue(attrvalue);
      attrs.push([attrname.toLowerCase(), attrvalue]);
      k = m.index + m[0].length;
    }
    const end = pyStrip(rawdata.slice(k, endpos));
    if (end !== ">" && end !== "/>") {
      this.handleData(rawdata.slice(i, endpos));
      return endpos;
    }
    if (end.endsWith("/>")) {
      // XHTML-style empty tag: handle_startendtag.
      this.handleStarttag(tag, attrs);
      this.handleEndtag(tag);
    } else {
      this.handleStarttag(tag, attrs);
      if (CDATA_CONTENT_ELEMENTS.has(tag) || tag === "plaintext") {
        this.setCdataMode(tag, false);
      } else if (RCDATA_CONTENT_ELEMENTS.has(tag)) {
        this.setCdataMode(tag, true);
      }
    }
    return endpos;
  }

  private checkForWholeStartTag(i: number): number {
    const m = at(LOCATETAGEND, this.rawdata, i + 1) as RegExpExecArray;
    const j = m.index + m[0].length;
    return this.rawdata.charAt(j - 1) !== ">" ? -1 : j;
  }

  private parseEndtag(i: number): number {
    const rawdata = this.rawdata;
    if (rawdata.indexOf(">", i + 2) < 0) return -1;
    if (!at(ENDTAGOPEN, rawdata, i)) {
      if (rawdata.charAt(i + 2) === ">") return i + 3; // "</>" is ignored
      return this.parseBogusComment(i);
    }
    const m = at(LOCATETAGEND, rawdata, i + 2) as RegExpExecArray;
    const j = m.index + m[0].length;
    if (rawdata.charAt(j - 1) !== ">") return -1;
    const tm = at(TAGFIND_TOLERANT, rawdata, i + 2) as RegExpExecArray;
    const tag = (tm[1] as string).toLowerCase();
    this.handleEndtag(tag);
    this.clearCdataMode();
    return j;
  }
}
