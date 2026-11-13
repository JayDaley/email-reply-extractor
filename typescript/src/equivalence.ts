/**
 * Whitespace-tolerant equivalence between two derived texts.
 *
 * Port of the reference's `equivalence.py`. The comparison: non-breaking
 * spaces unified to spaces, each line's edge whitespace stripped (the Python
 * `str.isspace()` set), blank lines dropped. Everything else is compared
 * exactly.
 *
 * @module
 */

import { pyStrip } from "./pyCompat.js";

/**
 * The non-blank lines of `text`, each with U+00A0 unified to a space and edge
 * whitespace stripped. Two texts with equal `tolerantLines` differ at most in
 * whitespace.
 */
export function tolerantLines(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split("\n")) {
    const line = pyStrip(raw.replaceAll("\u00a0", " "));
    if (line) out.push(line);
  }
  return out;
}

/** True when `a` and `b` differ at most in whitespace. */
export function textsEquivalent(a: string, b: string): boolean {
  const la = tolerantLines(a);
  const lb = tolerantLines(b);
  return la.length === lb.length && la.every((line, i) => line === lb[i]);
}
