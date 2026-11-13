/**
 * HTML-as-oracle: recover visible text and quote/signature structure from the
 * `text/html` part of a message.
 *
 * Port of the reference's `html_text.py`; its module docstring documents the
 * rules. Parsing uses {@link HtmlParser}, a port of the CPython
 * `html.parser.HTMLParser` tokenizer, so malformed markup is handled exactly
 * as the reference handles it.
 *
 * @module
 */

import { type Attr, HtmlParser } from "./htmlParser.js";
import { isBlank, pySplit } from "./pyCompat.js";

// --- categories ---------------------------------------------------------------

/** Text outside any recognized quoted/signature container — the author's own. */
const NOVEL = "novel";
/** Text inside a quoted container (quoted message, forwarded header, cite prefix). */
const QUOTED = "quoted";
/** Text inside a signature container that is not itself inside a quoted one. */
const SIGNATURE = "signature";

type Category = typeof NOVEL | typeof QUOTED | typeof SIGNATURE;

// --- tag sets -----------------------------------------------------------------

/** Block-level tags whose open and close each force a line break. */
const BLOCK_TAGS: ReadonlySet<string> = new Set([
  "p",
  "div",
  "li",
  "tr",
  "table",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "blockquote",
  "pre",
  "ul",
  "ol",
]);

/** Void / empty elements, never pushed onto the container stack. */
const VOID_TAGS: ReadonlySet<string> = new Set([
  "area",
  "base",
  "br",
  "col",
  "embed",
  "hr",
  "img",
  "input",
  "link",
  "meta",
  "param",
  "source",
  "track",
  "wbr",
]);

/** Elements whose text content is never visible and must be dropped entirely. */
const SKIP_TAGS: ReadonlySet<string> = new Set(["script", "style", "head"]);

// --- container hooks ----------------------------------------------------------

/** `class` tokens that mark a quoted container. */
const QUOTED_CLASSES: ReadonlySet<string> = new Set(["gmail_attr", "moz-cite-prefix", "OutlookMessageHeader"]);
/** `class` tokens for Gmail's quote wrapper `<div>` (see the reference). */
const GMAIL_WRAPPER_CLASSES: ReadonlySet<string> = new Set(["gmail_quote", "gmail_quote_container"]);
/** `id` values that mark a quoted container. */
const QUOTED_IDS: ReadonlySet<string> = new Set(["divRplyFwdMsg", "appendonsend"]);
/** `class` tokens that mark a signature container. */
const SIGNATURE_CLASSES: ReadonlySet<string> = new Set(["gmail_signature", "moz-signature", "Signature"]);
/** `id` value Outlook uses for its signature `<div>`. */
const SIGNATURE_ID = "Signature";

/** The visible text of an HTML part split by container category. */
export interface HtmlParts {
  /** Everything outside any quoted or signature container. */
  readonly novelText: string;
  /** Everything inside a quoted container (nesting included). */
  readonly quotedText: string;
  /** Everything inside a signature container that is not itself inside a quoted one. */
  readonly signatureText: string;
}

/** `dict(attrs)`: the last value of a duplicated attribute wins. */
function attrDict(attrs: readonly Attr[]): Map<string, string | null> {
  const d = new Map<string, string | null>();
  for (const [name, value] of attrs) d.set(name, value);
  return d;
}

function classesAndId(attrs: readonly Attr[]): [string[], string] {
  const d = attrDict(attrs);
  return [pySplit(d.get("class") || ""), d.get("id") || ""];
}

// --- parser -------------------------------------------------------------------

// Both parsers keep a stack of open elements, and a close tag pops the nearest
// open frame with its tag together with every frame above it. Scanning the
// stack for that frame costs no more than the frames it pops, except for a stray
// close tag with no open frame, which would scan the whole stack and pop nothing
// (quadratic on "<a>" * n + "</b>" * n). Each parser therefore also counts its
// open frames per tag, and a close tag whose count is zero is ignored without a
// scan.

/** Add `delta` to the open-frame count of `tag`. */
function countOpen(counts: Map<string, number>, tag: string, delta: number): void {
  counts.set(tag, (counts.get(tag) ?? 0) + delta);
}

/** Index of the nearest frame on `stack` whose tag is `tag`, or `-1`. */
function closeFrame(
  stack: readonly (readonly [string, ...unknown[]])[],
  counts: Map<string, number>,
  tag: string,
): number {
  if (!counts.get(tag)) return -1;
  for (let i = stack.length - 1; i >= 0; i--) {
    if ((stack[i] as readonly [string, ...unknown[]])[0] === tag) return i;
  }
  return -1; // unreachable while the counts mirror the stack
}

/** A segment: a category-less line break, or a run of text with its category. */
type Segment = { readonly kind: "nl" } | { readonly kind: "text"; readonly category: Category; readonly text: string };

const NL: Segment = Object.freeze({ kind: "nl" });

/** Collect category-tagged text/newline segments from an HTML document. */
class Segmenter extends HtmlParser {
  private readonly stack: [string, Category | null, boolean][] = [];
  /** Number of frames on `stack` per tag (see {@link closeFrame}). */
  private readonly openCounts = new Map<string, number>();
  private quotedDepth = 0;
  private signatureDepth = 0;
  private skipDepth = 0;
  private wrapperCount = 0;
  readonly segments: Segment[] = [];

  constructor(private readonly interleavedWrappers: ReadonlySet<number>) {
    super();
  }

  private activeCategory(): Category {
    if (this.quotedDepth > 0) return QUOTED;
    if (this.signatureDepth > 0) return SIGNATURE;
    return NOVEL;
  }

  private containerCategory(tag: string, attrs: readonly Attr[]): Category | null {
    const [classes, elId] = classesAndId(attrs);
    if (tag === "blockquote" || classes.some((c) => QUOTED_CLASSES.has(c)) || QUOTED_IDS.has(elId)) {
      return QUOTED;
    }
    if (tag !== "blockquote" && classes.some((c) => GMAIL_WRAPPER_CLASSES.has(c))) {
      const index = this.wrapperCount;
      this.wrapperCount++;
      return this.interleavedWrappers.has(index) ? null : QUOTED;
    }
    if (classes.some((c) => SIGNATURE_CLASSES.has(c)) || elId === SIGNATURE_ID) return SIGNATURE;
    return null;
  }

  protected handleStarttag(tagName: string, attrs: readonly Attr[]): void {
    const tag = tagName.toLowerCase();
    if (SKIP_TAGS.has(tag)) {
      this.skipDepth++;
      this.stack.push([tag, null, true]);
      countOpen(this.openCounts, tag, 1);
      return;
    }
    if (VOID_TAGS.has(tag)) {
      if (tag === "br" && this.skipDepth === 0) this.segments.push(NL);
      return;
    }
    const category = this.containerCategory(tag, attrs);
    this.stack.push([tag, category, false]);
    countOpen(this.openCounts, tag, 1);
    if (category === QUOTED) this.quotedDepth++;
    else if (category === SIGNATURE) this.signatureDepth++;
    if (BLOCK_TAGS.has(tag) && this.skipDepth === 0) this.segments.push(NL);
  }

  protected handleEndtag(tagName: string): void {
    const tag = tagName.toLowerCase();
    if (VOID_TAGS.has(tag)) return;
    // Find the nearest matching open frame; ignore a stray close with none.
    const idx = closeFrame(this.stack, this.openCounts, tag);
    if (idx < 0) return;
    // Pop that frame and any unclosed inner frames, adjusting the counters.
    for (let i = idx; i < this.stack.length; i++) {
      const [frameTag, category, isSkip] = this.stack[i] as [string, Category | null, boolean];
      countOpen(this.openCounts, frameTag, -1);
      if (isSkip) this.skipDepth--;
      else if (category === QUOTED) this.quotedDepth--;
      else if (category === SIGNATURE) this.signatureDepth--;
    }
    this.stack.length = idx;
    if (BLOCK_TAGS.has(tag) && this.skipDepth === 0) this.segments.push(NL);
  }

  protected handleData(data: string): void {
    if (this.skipDepth > 0) return;
    this.segments.push({ kind: "text", category: this.activeCategory(), text: data });
  }
}

// --- rendering ----------------------------------------------------------------

/** Render `segments` to text, keeping only `wanted`-category runs (`null` keeps all). */
function render(segments: readonly Segment[], wanted: Category | null): string {
  const lines: string[] = [];
  let buf = "";
  for (const seg of segments) {
    if (seg.kind === "nl") {
      lines.push(buf);
      buf = "";
    } else if (wanted === null || seg.category === wanted) {
      buf += seg.text;
    }
  }
  lines.push(buf);

  const collapsed: string[] = [];
  for (const raw of lines) {
    const line = pySplit(raw).join(" ");
    if (!line && collapsed.length > 0 && !collapsed[collapsed.length - 1]) continue;
    collapsed.push(line);
  }
  let start = 0;
  let end = collapsed.length;
  while (start < end && !collapsed[start]) start++;
  while (end > start && !collapsed[end - 1]) end--;
  return collapsed.slice(start, end).join("\n");
}

/** First pass: which Gmail wrapper instances hold a `<blockquote>`. */
class WrapperScanner extends HtmlParser {
  private readonly stack: [string, number | null][] = [];
  /** Number of frames on `stack` per tag (see {@link closeFrame}). */
  private readonly openCounts = new Map<string, number>();
  /** `[stack position, wrapper index]` of every open wrapper, bottom first. */
  private readonly openWrappers: [number, number][] = [];
  private wrapperCount = 0;
  readonly interleaved = new Set<number>();

  protected handleStarttag(tagName: string, attrs: readonly Attr[]): void {
    const tag = tagName.toLowerCase();
    if (VOID_TAGS.has(tag)) return;
    if (tag === "blockquote") {
      // Mark every open wrapper, from the innermost out. A marked wrapper was
      // marked by an earlier blockquote while every wrapper below it (opened
      // before it and still open) was open too, so those are marked already
      // and the walk stops there. Walking the whole stack on every blockquote
      // is quadratic.
      for (let w = this.openWrappers.length - 1; w >= 0; w--) {
        const index = (this.openWrappers[w] as [number, number])[1];
        if (this.interleaved.has(index)) break;
        this.interleaved.add(index);
      }
      this.stack.push([tag, null]);
      countOpen(this.openCounts, tag, 1);
      return;
    }
    const [classes, elId] = classesAndId(attrs);
    let index: number | null = null;
    const isQuotedHook = classes.some((c) => QUOTED_CLASSES.has(c)) || QUOTED_IDS.has(elId);
    if (!isQuotedHook && classes.some((c) => GMAIL_WRAPPER_CLASSES.has(c))) {
      index = this.wrapperCount;
      this.wrapperCount++;
      this.openWrappers.push([this.stack.length, index]);
    }
    this.stack.push([tag, index]);
    countOpen(this.openCounts, tag, 1);
  }

  protected handleEndtag(tagName: string): void {
    const tag = tagName.toLowerCase();
    if (VOID_TAGS.has(tag)) return;
    const idx = closeFrame(this.stack, this.openCounts, tag);
    if (idx < 0) return;
    for (let i = idx; i < this.stack.length; i++) {
      countOpen(this.openCounts, (this.stack[i] as [string, number | null])[0], -1);
    }
    this.stack.length = idx;
    while (
      this.openWrappers.length > 0 &&
      (this.openWrappers[this.openWrappers.length - 1] as [number, number])[0] >= idx
    ) {
      this.openWrappers.pop();
    }
  }

  protected handleData(_data: string): void {}
}

function interleavedWrappers(html: string): ReadonlySet<number> {
  const scanner = new WrapperScanner();
  try {
    scanner.parse(html);
  } catch {
    // html.parser is lenient, but stay defensive (as the reference does).
  }
  return scanner.interleaved;
}

function segment(html: string): Segment[] {
  const parser = new Segmenter(interleavedWrappers(html));
  try {
    parser.parse(html);
  } catch {
    // Keep the segments collected before the error, as the reference does.
  }
  return parser.segments;
}

// --- public API ---------------------------------------------------------------

/**
 * Return the visible text of `html` with block structure as newlines.
 *
 * `<br>` and block-level element open/close become line breaks; `script` /
 * `style` / `head` content and comments are dropped; character references are
 * unescaped; each line has its internal whitespace collapsed, blank runs
 * collapse to one, and blank edges are trimmed. Never throws on malformed HTML
 * (empty or blank input yields `""`).
 */
export function htmlToText(html: string): string {
  if (!html || isBlank(html)) return "";
  return render(segment(html), null);
}

/**
 * Split `html` into novel / quoted / signature visible-text streams.
 *
 * Rendering rules match {@link htmlToText}; each stream keeps the text whose
 * innermost enclosing container has that category (see the reference's
 * `split_html_parts` for the container rules). Never throws on malformed HTML;
 * empty or blank input yields three empty strings.
 */
export function splitHtmlParts(html: string): HtmlParts {
  if (!html || isBlank(html)) {
    return Object.freeze({ novelText: "", quotedText: "", signatureText: "" });
  }
  const segments = segment(html);
  return Object.freeze({
    novelText: render(segments, NOVEL),
    quotedText: render(segments, QUOTED),
    signatureText: render(segments, SIGNATURE),
  });
}
