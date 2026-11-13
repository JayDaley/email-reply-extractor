/**
 * Fragment scanner: segment an email body into quoted / unquoted fragments.
 *
 * Port of the reference's `_fragments.py`, which is vendored and trimmed from
 * `email_reply_parser` 0.5.12 (Zapier's Python port of GitHub's Email Reply
 * Parser, MIT License, Copyright (c) 2012 Zapier LLC). See that module's
 * docstring for the scan and for the two upstream defects fixed in
 * generation 5.
 *
 * The wrapped-attribution collapse passes the first "On … wrote:" match to
 * `re.sub` as a replacement *template*; {@link expandTemplate} reproduces
 * Python's template rules, including the `re.error` a bad escape raises.
 *
 * @internal
 */

import { isBlank, PyPattern, pyStrip } from "./pyCompat.js";

const SIG_RE = new PyPattern(String.raw`(--|__|-\w)|(^Sent from my (\w+\s*){1,3})`);
const QUOTE_HDR_RE = new PyPattern("On.*wrote:$");
const QUOTED_RE = new PyPattern("(>+)");
const HEADER_RE = new PyPattern(String.raw`^\*?(From|Sent|To|Subject):\*? .+`);
const MULTI_QUOTE_HDR = String.raw`(?!On.*On\s.+?wrote:)(On\s(.+?)wrote:)`;
/** Upstream's `MULTI_QUOTE_HDR_REGEX` (DOTALL | MULTILINE; no anchors, so MULTILINE is inert). */
const MULTI_QUOTE_HDR_SUB_RE = new PyPattern(MULTI_QUOTE_HDR, { dotAll: true });
/** Upstream's `MULTI_QUOTE_HDR_REGEX_MULTILINE` (DOTALL). */
const MULTI_QUOTE_HDR_SEARCH_RE = MULTI_QUOTE_HDR_SUB_RE;
/** Number of groups in {@link MULTI_QUOTE_HDR}. */
const MULTI_QUOTE_HDR_GROUPS = 2;
const OUTLOOK_BOUNDARY_RE = new PyPattern("([^\n])(?=\n ?[_-]{7,})");

/** One scanned fragment: its content (edges kept) and whether it is quoted. */
export interface Fragment {
  readonly content: string;
  readonly quoted: boolean;
}

/** Python's `re.error` (`re.PatternError`). */
export class PatternError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PatternError";
  }
}

/** Python's `IndexError`, raised by a template's unknown group name. */
export class TemplateIndexError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IndexError";
  }
}

const OCTDIGITS = "01234567";
const DIGITS = "0123456789";
const ESCAPES: Readonly<Record<string, string>> = {
  a: "\x07",
  b: "\b",
  f: "\f",
  n: "\n",
  r: "\r",
  t: "\t",
  v: "\v",
  "\\": "\\",
};

/** A parsed template: literal strings and group indices. */
type Template = (string | number)[];

function isIdentifier(name: string): boolean {
  // str.isidentifier(): XID_Start (or "_") then XID_Continue.
  return /^[\p{XID_Start}_][\p{XID_Continue}]*$/u.test(name);
}

/** `re._parser.parse_template(source, pattern)` for a pattern with `groups` groups and no names. */
export function parseTemplate(source: string, groups: number): Template {
  const result: Template = [];
  let literal = "";
  let index = 0;
  // Tokenizer: `next` is one character, or a backslash plus the following character.
  let next: string | null = null;
  const advance = (): void => {
    if (index >= source.length) {
      next = null;
      return;
    }
    let ch = source.charAt(index);
    if (ch === "\\") {
      index++;
      if (index >= source.length) throw new PatternError("bad escape (end of pattern)");
      ch += source.charAt(index);
    }
    index++;
    next = ch;
  };
  const get = (): string | null => {
    const t = next;
    advance();
    return t;
  };
  const addgroup = (n: number): void => {
    if (n > groups) throw new PatternError(`invalid group reference ${n}`);
    if (literal) result.push(literal);
    literal = "";
    result.push(n);
  };
  advance();
  for (;;) {
    let t = get();
    if (t === null) break;
    if (t.charAt(0) !== "\\") {
      literal += t;
      continue;
    }
    const c = t.charAt(1);
    if (c === "g") {
      if (next !== "<") throw new PatternError("missing <");
      advance();
      let name = "";
      for (;;) {
        const ch: string | null = next;
        advance();
        if (ch === null) {
          throw new PatternError(name ? "missing >, unterminated name" : "missing group name");
        }
        if (ch === ">") {
          if (!name) throw new PatternError("missing group name");
          break;
        }
        name += ch;
      }
      if (/^[0-9]+$/.test(name)) {
        addgroup(Number.parseInt(name, 10));
      } else {
        if (!isIdentifier(name)) throw new PatternError(`bad character in group name '${name}'`);
        throw new TemplateIndexError(`unknown group name '${name}'`);
      }
    } else if (c === "0") {
      if (next !== null && OCTDIGITS.includes(next)) {
        t += get();
        if (next !== null && OCTDIGITS.includes(next)) t += get();
      }
      literal += String.fromCharCode(Number.parseInt(t.slice(1), 8) & 0xff);
    } else if (DIGITS.includes(c)) {
      let isoctal = false;
      if (next !== null && DIGITS.includes(next)) {
        t += get();
        if (OCTDIGITS.includes(c) && OCTDIGITS.includes(t.charAt(2)) && next !== null && OCTDIGITS.includes(next)) {
          t += get();
          isoctal = true;
          const value = Number.parseInt(t.slice(1), 8);
          if (value > 0o377) throw new PatternError(`octal escape value ${t} outside of range 0-0o377`);
          literal += String.fromCharCode(value);
        }
      }
      if (!isoctal) addgroup(Number.parseInt(t.slice(1), 10));
    } else {
      const esc = ESCAPES[c];
      if (esc !== undefined) {
        literal += esc;
      } else {
        if (/^[A-Za-z]$/.test(c)) throw new PatternError(`bad escape ${t}`);
        literal += t;
      }
    }
  }
  if (literal) result.push(literal);
  return result;
}

function expand(template: Template, m: RegExpExecArray): string {
  let out = "";
  for (const part of template) {
    out += typeof part === "number" ? (m[part] ?? "") : part;
  }
  return out;
}

/** Scan `text` (LF-normalized) into document-order fragments. */
export function readFragments(text: string): Fragment[] {
  const match = MULTI_QUOTE_HDR_SEARCH_RE.search(text);
  if (match) {
    // Collapse a wrapped multi-line "On … wrote:" header onto one line; the
    // matched text is a replacement *template* (see the module comment).
    const template = parseTemplate((match[1] as string).replaceAll("\n", ""), MULTI_QUOTE_HDR_GROUPS);
    text = MULTI_QUOTE_HDR_SUB_RE.sub(text, (m) => expand(template, m));
  }

  // Break the reply off an Outlook-style boundary line it sits directly on top
  // of — every occurrence.
  text = OUTLOOK_BOUNDARY_RE.sub(text, (m) => `${m[1]}\n`);

  const fragments: Fragment[] = [];
  let open: OpenFragment | null = null;
  const lines = text.split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    open = scanLine(fragments, open, lines[i] as string);
  }
  if (open !== null) fragments.push(close(open));
  fragments.reverse();
  return fragments;
}

interface OpenFragment {
  readonly quoted: boolean;
  readonly headers: boolean;
  readonly lines: string[];
}

function close(f: OpenFragment): Fragment {
  // Lines were appended in bottom-up scan order; restore document order.
  return Object.freeze({ content: [...f.lines].reverse().join("\n"), quoted: f.quoted });
}

function scanLine(fragments: Fragment[], open: OpenFragment | null, line: string): OpenFragment | null {
  const isQuoteHeader = QUOTE_HDR_RE.test(line);
  const isQuoted = QUOTED_RE.test(line);
  const isHeader = isQuoteHeader || HEADER_RE.test(line);
  const blank = isBlank(line);

  // A blank line directly above a signature-looking line closes the fragment.
  if (open !== null && blank) {
    const last = open.lines[open.lines.length - 1] as string;
    if (SIG_RE.test(pyStrip(last))) {
      fragments.push(close(open));
      open = null;
    }
  }

  if (
    open !== null &&
    ((open.headers === isHeader && open.quoted === isQuoted) || (open.quoted && (isQuoteHeader || blank)))
  ) {
    open.lines.push(line);
    return open;
  }

  if (open !== null) fragments.push(close(open));
  return { quoted: isQuoted, headers: isHeader, lines: [line] };
}
