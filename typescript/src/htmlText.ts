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

/** A segment: a category-less line break, or a run of text with its category. */
type Segment = { readonly kind: "nl" } | { readonly kind: "text"; readonly category: Category; readonly text: string };

const NL: Segment = Object.freeze({ kind: "nl" });

/** Collect category-tagged text/newline segments from an HTML document. */
class Segmenter extends HtmlParser {
  private readonly stack: [string, Category | null, boolean][] = [];
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
      return;
    }
    if (VOID_TAGS.has(tag)) {
      if (tag === "br" && this.skipDepth === 0) this.segments.push(NL);
      return;
    }
    const category = this.containerCategory(tag, attrs);
    this.stack.push([tag, category, false]);
    if (category === QUOTED) this.quotedDepth++;
    else if (category === SIGNATURE) this.signatureDepth++;
    if (BLOCK_TAGS.has(tag) && this.skipDepth === 0) this.segments.push(NL);
  }

  protected handleEndtag(tagName: string): void {
    const tag = tagName.toLowerCase();
    if (VOID_TAGS.has(tag)) return;
    let idx = -1;
    for (let i = this.stack.length - 1; i >= 0; i--) {
      if ((this.stack[i] as [string, Category | null, boolean])[0] === tag) {
        idx = i;
        break;
      }
    }
    if (idx < 0) return;
    for (const [, category, isSkip] of this.stack.slice(idx)) {
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
  private wrapperCount = 0;
  readonly interleaved = new Set<number>();

  protected handleStarttag(tagName: string, attrs: readonly Attr[]): void {
    const tag = tagName.toLowerCase();
    if (VOID_TAGS.has(tag)) return;
    if (tag === "blockquote") {
      for (const [, index] of this.stack) {
        if (index !== null) this.interleaved.add(index);
      }
      this.stack.push([tag, null]);
      return;
    }
    const [classes, elId] = classesAndId(attrs);
    let index: number | null = null;
    const isQuotedHook = classes.some((c) => QUOTED_CLASSES.has(c)) || QUOTED_IDS.has(elId);
    if (!isQuotedHook && classes.some((c) => GMAIL_WRAPPER_CLASSES.has(c))) {
      index = this.wrapperCount;
      this.wrapperCount++;
    }
    this.stack.push([tag, index]);
  }

  protected handleEndtag(tagName: string): void {
    const tag = tagName.toLowerCase();
    if (VOID_TAGS.has(tag)) return;
    for (let i = this.stack.length - 1; i >= 0; i--) {
      if ((this.stack[i] as [string, number | null])[0] === tag) {
        this.stack.length = i;
        return;
      }
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
