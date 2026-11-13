/**
 * Fragment scanner: segment an email body into quoted / unquoted fragments.
 *
 * Port of the reference's `_fragments.py`, which is vendored and trimmed from
 * `email_reply_parser` 0.5.12 (Zapier's Python port of GitHub's Email Reply
 * Parser, MIT License, Copyright (c) 2012 Zapier LLC). See that module's
 * docstring for the scan and for the upstream defects it fixes.
 *
 * The wrapped-attribution collapse inserts the first "On … wrote:" match,
 * with its newlines removed, literally in place of the match; upstream used
 * it as a Python `re.sub` replacement template.
 *
 * @internal
 */

import { isBlank, PyPattern, pyStrip } from "./pyCompat.js";

const SIG_RE = new PyPattern(String.raw`(--|__|-\w)|(^Sent from my (\w+\s*){1,3})`);
const QUOTE_HDR_RE = new PyPattern("On.*wrote:$");
const QUOTED_RE = new PyPattern("(>+)");
const HEADER_RE = new PyPattern(String.raw`^\*?(From|Sent|To|Subject):\*? .+`);
/**
 * Upstream's `MULTI_QUOTE_HDR_REGEX` (DOTALL | MULTILINE, used for the
 * substitution) and `MULTI_QUOTE_HDR_REGEX_MULTILINE` (DOTALL, used for the
 * search). Kept for reference only: run by the regex engine over a whole body,
 * the lookahead makes it cubic in the number of "On" + whitespace positions.
 * {@link findMultiQuoteHeader} computes the same match in linear time.
 */
export const MULTI_QUOTE_HDR = String.raw`(?!On.*On\s.+?wrote:)(On\s(.+?)wrote:)`;
/** An "On" followed by whitespace (`\s` as in {@link MULTI_QUOTE_HDR}). */
const ON_SPACE_RE = new PyPattern(String.raw`On(?=\s)`);
/**
 * {@link MULTI_QUOTE_HDR} without the lookahead. Applied with `match` at the
 * one start position {@link findMultiQuoteHeader} finds; the lazy `.+?` stops
 * at the first `wrote:`, so it is linear.
 */
const MULTI_QUOTE_HDR_AT_RE = new PyPattern(String.raw`On\s.+?wrote:`, { dotAll: true });
const OUTLOOK_BOUNDARY_RE = new PyPattern("([^\n])(?=\n ?[_-]{7,})");

/** One scanned fragment: its content (edges kept) and whether it is quoted. */
export interface Fragment {
  readonly content: string;
  readonly quoted: boolean;
}

/**
 * The match of {@link MULTI_QUOTE_HDR} in `text`, found in linear time.
 *
 * The pattern matches `On\s` at a position `p`, then the first `wrote:`
 * starting at `p + 4` or later (`.+?` needs one character). Its lookahead
 * rejects `p` when another such `On\s` at `q >= p + 2` also has a `wrote:`
 * starting at `q + 4` or later. With `W` the start of the last `wrote:`, the
 * qualifying positions are the `On\s` positions `p <= W - 4`, and the lookahead
 * accepts only the last of them (two "On" occurrences are never one character
 * apart, so every earlier one has a later one at `q >= p + 2`). That position
 * is the only match, both for `search` and for every match `sub` would
 * replace. In UTF-16 units the bound is the same: `p <= W - 4` says at least
 * one code unit, hence at least one code point, lies between `On\s` and
 * `wrote:`, and `\s` is always a single BMP code unit.
 */
function findMultiQuoteHeader(text: string): RegExpExecArray | null {
  const lastWrote = text.lastIndexOf("wrote:");
  if (lastWrote < 0) return null;
  let start = -1;
  for (let on = ON_SPACE_RE.search(text); on !== null; on = ON_SPACE_RE.search(text, on.index + 2)) {
    if (on.index > lastWrote - 4) break;
    start = on.index;
  }
  if (start < 0) return null;
  return MULTI_QUOTE_HDR_AT_RE.match(text, start);
}

/** Scan `text` (LF-normalized) into document-order fragments. */
export function readFragments(text: string): Fragment[] {
  const match = findMultiQuoteHeader(text);
  if (match) {
    // Collapse a wrapped multi-line "On … wrote:" header onto one line.
    // Upstream ran `re.sub` with the pattern; it has exactly one match (see
    // findMultiQuoteHeader), so the matched text, with its newlines removed,
    // is spliced in place of the match. It is inserted literally, not as a
    // replacement template (see the module comment).
    const collapsed = match[0].replaceAll("\n", "");
    text = text.slice(0, match.index) + collapsed + text.slice(match.index + match[0].length);
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
