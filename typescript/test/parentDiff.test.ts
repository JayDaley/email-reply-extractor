// The parent-diff assist's matching structures (generation 7).
//
// Mirrors python/tests/test_parent_diff.py, whose tie-break cases are also
// replayed as conformance vectors:
//
// - Rule (a), the alignment around the longest common run of lines
//   (alignedRunMarks): a brute-force transcription of the specification
//   compared on random inputs.
// - Rules (b) and (c), the substring index over the parent's word-stream
//   (ParentStream): its answers equal a code-point substring search on every
//   path, unpaired surrogates included.
// - The inline-reply author-tag test (anyAuthorTag): its answers equal the
//   per-label substring and regular-expression tests it replaced.

import assert from "node:assert/strict";
import { test } from "node:test";

import { alignedRunMarks, anyAuthorTag, ParentStream } from "../src/extraction.js";
import { escapeRegex, pySplit, pyStrip, WORD_RE_CLASS } from "../src/pyCompat.js";

/** A seeded PRNG (mulberry32). */
function rng(seed: number): { int: (n: number) => number; real: () => number } {
  let s = seed;
  const real = (): number => {
    s = (s + 0x6d2b79f5) | 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return { int: (n) => Math.floor(real() * n), real };
}

// --- rule (a): brute-force reference ------------------------------------------------

/** The specification of rule (a), transcribed literally: every run and every occurrence is enumerated. */
function referenceMarks(c: string[], p: string[]): boolean[] {
  const n = c.length;
  const m = p.length;
  const marks = new Array<boolean>(n).fill(false);
  const words = c.map((line) => pySplit(line).length);
  const occurrences = (s: number, e: number, lo: number, hi: number): number[] => {
    const out: number[] = [];
    for (let j = lo; j <= hi - (e - s); j++) {
      let same = true;
      for (let k = 0; k < e - s && same; k++) same = p[j + k] === c[s + k];
      if (same) out.push(j);
    }
    return out;
  };
  const substance = (s: number, e: number): boolean => {
    const slice = words.slice(s, e);
    return slice.reduce((a, b) => a + b, 0) >= 10 || slice.some((w) => w >= 8);
  };
  let best = 0;
  let i = -1;
  for (let s = 0; s < n; s++) {
    for (let e = s + 1; e <= n; e++) {
      if (e - s > best && occurrences(s, e, 0, m).length > 0) {
        best = e - s;
        i = s;
      }
    }
  }
  if (best === 0) return marks;
  const end = i + best;
  const j = occurrences(i, end, 0, m)[0] as number;
  if (substance(i, end)) for (let x = i; x < end; x++) marks[x] = true;
  for (const [cLo, cHi, pLo, pHi] of [
    [0, i, 0, j],
    [end, n, j + best, m],
  ] as const) {
    for (let s = cLo; s < cHi; s++) {
      for (let e = s + 1; e <= cHi; e++) {
        if (substance(s, e) && occurrences(s, e, pLo, pHi).length > 0) for (let x = s; x < e; x++) marks[x] = true;
      }
    }
  }
  return marks;
}

test("alignedRunMarks matches the brute-force reference on random inputs", () => {
  const r = rng(20261114);
  for (let round = 0; round < 3000; round++) {
    const alphabet = Array.from({ length: 1 + r.int(6) }, (_, a) => {
      const words = [1, 1, 2, 3, 4, 5, 7, 8, 9][r.int(9)] as number;
      return `s${a}${" w".repeat(words - 1)}`;
    });
    const pick = (): string => alphabet[r.int(alphabet.length)] as string;
    let c = Array.from({ length: r.int(9) }, pick);
    const p = Array.from({ length: r.int(9) }, pick);
    if (p.length > 0 && r.real() < 0.5) {
      const a = r.int(p.length);
      const b = a + 1 + r.int(p.length - a);
      const at = r.int(c.length + 1);
      c = [...c.slice(0, at), ...p.slice(a, b), ...c.slice(at)];
    }
    assert.deepStrictEqual(alignedRunMarks(c, p), referenceMarks(c, p), JSON.stringify([c, p]));
  }
});

// --- rules (b) and (c): the parent word-stream index ------------------------------

/** `needle in stream` over code points (a lone surrogate is one code point), as Python compares. */
function cpIncludes(stream: string, needle: string): boolean {
  const s = Array.from(stream);
  const n = Array.from(needle);
  for (let at = 0; at + n.length <= s.length; at++) {
    let same = true;
    for (let k = 0; k < n.length && same; k++) same = s[at + k] === n[k];
    if (same) return true;
  }
  return false;
}

test("ParentStream answers equal a code-point substring search", () => {
  const r = rng(7);
  const vocabs = [
    ["a", "b"],
    ["a", "ab", "ba", "b"],
    ["x", "xy", "yx", "y", "z", "é", "\u{1f600}"],
    // Unpaired surrogates next to the halves of a pair.
    ["\u{1f600}", "\ud83d", "\ude00", "a\ude00", "\ud83da", "a"],
  ];
  for (let round = 0; round < 4000; round++) {
    const vocab = vocabs[r.int(vocabs.length)] as string[];
    const pick = (): string => vocab[r.int(vocab.length)] as string;
    const words = Array.from({ length: r.int(31) }, pick);
    const parent: string[] = [];
    for (let k = 0; k < words.length; ) {
      const step = 1 + r.int(5);
      parent.push(words.slice(k, k + step).join(" "));
      k += step;
    }
    const stream = parent.join(" ");
    const index = new ParentStream(parent);
    for (let q = 0; q < 12; q++) {
      let needle: string;
      if (stream && r.real() < 0.5) {
        // A slice of the stream (in code units, so it can split a pair), trimmed to needle form.
        const lo = r.int(stream.length);
        const hi = lo + 1 + r.int(stream.length - lo);
        needle = stream.slice(lo, hi).replace(/^ +| +$/g, "");
        if (!needle || needle.includes("  ")) continue;
        if (r.real() < 0.3) {
          const at = r.int(needle.length);
          if (needle[at] !== " ") needle = needle.slice(0, at) + pick().charAt(0) + needle.slice(at + 1);
        }
      } else {
        needle = Array.from({ length: 1 + r.int(8) }, pick).join(" ");
      }
      assert.equal(index.contains(needle), cpIncludes(stream, needle), JSON.stringify([parent, needle]));
    }
  }
});

test("ParentStream takes each path", () => {
  // The stream is "so the cat sat and the cat ran": the words "the" and "cat"
  // and the pair "the cat" occur twice, every other word and pair once.
  const index = new ParentStream(["so the cat sat", "and the cat ran"]);
  assert.ok(!index.contains(`${"x ".repeat(20)}x`)); // longer than the stream
  // Three words: the interior word decides when absent or unique.
  assert.ok(!index.contains("so dog cat"));
  assert.ok(index.contains("t sat a"));
  assert.ok(!index.contains("xx so the")); // the only start precedes the stream
  // Four or more words: the interior pairs decide when absent or unique.
  assert.ok(!index.contains("so the sat and"));
  assert.ok(index.contains("o the cat sat a"));
  assert.ok(!index.contains("so the cat sat on"));
  // Every interior gram repeated: a match at the first occurrence decides.
  assert.ok(index.contains("o the cat s"));
  // Every interior gram repeated and no match at the first occurrence: the automaton decides.
  assert.ok(index.contains("d the cat r"));
  assert.ok(!index.contains("d the cat s"));
  assert.ok(!index.contains("xso the cat"));
  // One or two words: the automaton, including matches inside words.
  assert.ok(index.contains("at an"));
  assert.ok(!index.contains("ran so"));
  assert.ok(index.contains("t"));
});

test("ParentStream does not match half of a surrogate pair", () => {
  // A unit-level match that starts or ends inside a pair is not an occurrence
  // in code points: on the table's unique-gram and candidate paths, and in the
  // automaton.
  // Unique interior gram; the unit match starts, or ends, inside a pair.
  assert.ok(!new ParentStream(["\u{1f600} b c"]).contains("\ude00 b c"));
  assert.ok(!new ParentStream(["a b \u{1f600}"]).contains("a b \ud83d"));
  // Repeated interior gram: the candidate's unit match starts, or ends, inside a pair.
  assert.ok(!new ParentStream(["\u{1f600} b c", "\u{1f600} b c"]).contains("\ude00 b c"));
  assert.ok(!new ParentStream(["a b \u{1f600}", "a b \u{1f600}"]).contains("a b \ud83d"));
  // The automaton.
  const index = new ParentStream(["a \u{1f600} b c", "x b c \u{1f600}"]);
  assert.ok(!index.contains("\ude00 b"));
  assert.ok(!index.contains("\ud83d"));
  assert.ok(index.contains("\u{1f600} b c"));
  assert.ok(index.contains("b c \u{1f600}"));
  const lone = new ParentStream(["a \ud83d b c", "\ude00 b c"]);
  assert.ok(lone.contains("\ud83d b c"));
  assert.ok(lone.contains("\ude00 b c"));
  assert.ok(!lone.contains("\u{1f600} b c"));
});

test("ParentStream reads a sliced needle that ends inside a pair as unpaired", () => {
  // A needle cut from a longer string shares its storage; once contains() is
  // optimized, String.prototype.codePointAt at its last index read the low
  // surrogate past its end on Node.js 26.5 (see pyCompat's codePointAt).
  const index = new ParentStream([`${"x".repeat(20)}\u{1f600}`]);
  const base = `${"x".repeat(40)}\u{1f600}`;
  for (let r = 0; r < 100000; r++) {
    const needle = base.slice(r % 25, 41); // ends with the high surrogate alone
    if (index.contains(needle)) assert.fail(`round ${r}: ${JSON.stringify(needle)}`);
  }
});

// --- the inline-reply author-tag index --------------------------------------------

const nameRegexes = new Map<string, RegExp>();

/** The generation-6 per-label tests. */
function referenceAnyAuthorTag(labels: string[], head: string, parentBody: string | null): boolean {
  for (const label of labels) {
    if (parentBody !== null && !cpIncludes(parentBody, `[${label}`)) return true;
    let name = label;
    while (/[0-9]$/.test(name)) name = name.slice(0, -1);
    name = pyStrip(name);
    if (!name) continue;
    let re = nameRegexes.get(name);
    if (re === undefined) {
      re = new RegExp(`(?<!${WORD_RE_CLASS})${escapeRegex(name)}(?!${WORD_RE_CLASS})`, "u");
      nameRegexes.set(name, re);
    }
    if (re.test(head)) return true;
  }
  return false;
}

test("anyAuthorTag equals the per-label tests", () => {
  // Word characters (letters, digits, "_", a letter with a diacritic, an
  // Arabic-Indic digit, an astral letter) and non-word characters.
  const tagAlphabet = ["a", "A", "b", "M", "1", "_", "é", "٣", " ", ".", "-", "\u{1d49c}", "\u{1f600}"];
  const textAlphabet = [...tagAlphabet, "[", "\n"];
  const r = rng(11);
  const string = (alphabet: string[], n: number): string =>
    Array.from({ length: n }, () => alphabet[r.int(alphabet.length)]).join("");
  for (let round = 0; round < 4000; round++) {
    const labels = Array.from({ length: 1 + r.int(3) }, () => string(tagAlphabet, 1 + r.int(4)));
    const head = string(textAlphabet, r.int(31));
    let parent: string | null = null;
    if (r.real() >= 0.2) {
      parent = string(textAlphabet, r.int(31));
      for (const label of labels) {
        if (r.real() < 0.7) {
          const chars: string[] = Array.from(parent);
          const at = r.int(chars.length + 1);
          parent = `${chars.slice(0, at).join("")}[${label}${chars.slice(at).join("")}`;
        }
      }
    }
    const want = referenceAnyAuthorTag(labels, head, parent);
    assert.equal(anyAuthorTag(labels, head, parent), want, JSON.stringify([labels, head, parent]));
  }
});

test("anyAuthorTag cases", () => {
  // A label the parent never uses is the author's.
  assert.ok(anyAuthorTag(["Med"], "", "no tags here"));
  // A label the parent uses belongs to the author only when its name (without
  // a round number) is a word of the head.
  assert.ok(anyAuthorTag(["Med2"], "Cheers,\nMed", "[Med2] earlier"));
  assert.ok(!anyAuthorTag(["Med"], "Cheers,\nMedina", "[Med] earlier"));
  assert.ok(!anyAuthorTag(["Med"], "Cheers,\n_Med", "[Med] earlier"));
  // Names may begin or end with non-word characters; the boundary is tested
  // on the characters around the occurrence only.
  assert.ok(anyAuthorTag(["J. D."], "-- J. D. x", "[J. D.]"));
  assert.ok(!anyAuthorTag(["J. D."], "-- J. D.x", "[J. D.]"));
  assert.ok(!anyAuthorTag(["J. D."], "-- xJ. D.", "[J. D.]"));
  // A label that is only digits has no name.
  assert.ok(!anyAuthorTag(["12"], "12", "[12]"));
  // Without the parent only the head decides.
  assert.ok(anyAuthorTag(["Linda3"], "Linda", null));
  assert.ok(!anyAuthorTag(["Linda3"], "Lindas", null));
  // Lengths and boundaries are in code points: an astral letter is one word
  // character, and a 20-code-point label can be 40 code units.
  assert.ok(!anyAuthorTag(["Med"], "\u{1d49c}Med", null));
  assert.ok(anyAuthorTag(["Med"], "\u{1f600}Med", null));
  const astral = "\u{1d49c}".repeat(20);
  assert.ok(!anyAuthorTag([astral], "", `[${astral}]`));
  assert.ok(anyAuthorTag([astral], astral, `x [${astral}`));
});
