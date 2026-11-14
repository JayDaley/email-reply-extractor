/**
 * Hash tables for the parent-diff assist's matching structures (see the
 * reference's `_build_automaton`, `_intern_lines` and `_ParentStream`).
 *
 * The reference uses `dict` and `set`. A JavaScript `Map` or `Set` holds at
 * most 2^24 entries and costs about a hundred bytes per entry, and a `Map` per
 * automaton state would cost more again; inputs of a few tens of megabytes
 * reach those limits, which the reference does not have. These tables keep
 * their entries in typed arrays (open addressing, linear probing) and have no
 * size limit below the size of the inputs. Their hash is seeded once per
 * process from `Math.random`, so an input cannot be built in advance to
 * collide in it. No result depends on the seed: lookups compare keys exactly,
 * and no caller iterates a table.
 *
 * @internal
 */

/** The per-process hash seed. */
const SEED = (Math.random() * 0x100000000) | 0;

/** MurmurHash3's 32-bit finalizer: a bijection that mixes every input bit. */
function mix(h: number): number {
  let x = h;
  x ^= x >>> 16;
  x = Math.imul(x, 0x85ebca6b);
  x ^= x >>> 13;
  x = Math.imul(x, 0xc2b2ae35);
  x ^= x >>> 16;
  return x;
}

/** The state of {@link hashUnits} before any code unit. */
export const HASH_START = SEED;

/** The state of {@link hashUnits} after absorbing the code units `s[from:to]` into state `h`. */
export function absorb(h: number, s: string, from: number, to: number): number {
  let x = h;
  for (let i = from; i < to; i++) {
    x = Math.imul(x ^ s.charCodeAt(i), 0x5bd1e995);
    x ^= x >>> 15;
  }
  return x;
}

/** The hash of `length` code units absorbed into state `h`. */
export function finish(h: number, length: number): number {
  return mix(h ^ length);
}

/** Hash of the UTF-16 code units `s[from:to]`. */
export function hashUnits(s: string, from: number, to: number): number {
  return finish(absorb(HASH_START, s, from, to), to - from);
}

/** Hash of a pair of 32-bit integers. For a fixed `a`, distinct `b` give distinct hashes. */
function hashPair(a: number, b: number): number {
  return mix((mix(a ^ SEED) + Math.imul(b ^ SEED, 0x9e3779b1)) | 0);
}

/** The smallest power of two that is at least `n` and at least 16. */
function capacityFor(n: number): number {
  let cap = 16;
  while (cap < n) cap *= 2;
  return cap;
}

function grown(array: Int32Array, size: number): Int32Array {
  const out = new Int32Array(size);
  out.set(array);
  return out;
}

// --- automaton transitions ------------------------------------------------------------

/**
 * The transitions of a suffix automaton: a map from (state, symbol) to state,
 * the reference's `trans[state][symbol]`. Symbols are 32-bit integers (line
 * ids or code points); -1 is never stored. Each state's transitions are also
 * chained in a list, so that a clone can copy them (`dict(trans[q])`).
 */
export class Transitions {
  /** Per state: its most recently added transition, or -1. */
  private head: Int32Array;
  /** Per transition: source state, symbol, target state, next of the same state. */
  private owner: Int32Array;
  private symbol: Int32Array;
  private target: Int32Array;
  private next: Int32Array;
  private count = 0;
  /** Open-addressing index of the transitions: a transition number, or -1. */
  private slots: Int32Array;
  private mask: number;

  /** A table for at most `states` states; `hint` is the expected number of transitions. */
  constructor(states: number, hint: number) {
    this.head = new Int32Array(Math.max(states, 1)).fill(-1);
    const size = Math.max(hint, 8);
    this.owner = new Int32Array(size);
    this.symbol = new Int32Array(size);
    this.target = new Int32Array(size);
    this.next = new Int32Array(size);
    this.slots = new Int32Array(capacityFor(2 * size)).fill(-1);
    this.mask = this.slots.length - 1;
  }

  /** The slot holding (`state`, `sym`), or the empty slot where it would go. */
  private slotOf(state: number, sym: number): number {
    const slots = this.slots;
    const mask = this.mask;
    let i = hashPair(state, sym) & mask;
    for (;;) {
      const t = slots[i] as number;
      if (t < 0 || (this.owner[t] === state && this.symbol[t] === sym)) return i;
      i = (i + 1) & mask;
    }
  }

  /** `trans[state].get(sym)`, with -1 for absent. */
  get(state: number, sym: number): number {
    const t = this.slots[this.slotOf(state, sym)] as number;
    return t < 0 ? -1 : (this.target[t] as number);
  }

  /** `trans[state][sym] = to`. */
  set(state: number, sym: number, to: number): void {
    const slot = this.slotOf(state, sym);
    const t = this.slots[slot] as number;
    if (t >= 0) {
      this.target[t] = to;
      return;
    }
    if (this.count === this.owner.length) {
      this.grow();
      this.add(state, sym, to);
      return;
    }
    this.insert(slot, state, sym, to);
  }

  /** Give the transition-less state `to` a copy of the transitions of `from` (`dict(trans[from])`). */
  copy(from: number, to: number): void {
    for (let t = this.head[from] as number; t >= 0; t = this.next[t] as number) {
      this.add(to, this.symbol[t] as number, this.target[t] as number);
    }
  }

  /** Insert a transition known to be absent. */
  private add(state: number, sym: number, to: number): void {
    if (this.count === this.owner.length) this.grow();
    this.insert(this.slotOf(state, sym), state, sym, to);
  }

  private insert(slot: number, state: number, sym: number, to: number): void {
    const t = this.count++;
    this.owner[t] = state;
    this.symbol[t] = sym;
    this.target[t] = to;
    this.next[t] = this.head[state] as number;
    this.head[state] = t;
    this.slots[slot] = t;
  }

  private grow(): void {
    const size = this.owner.length * 2;
    this.owner = grown(this.owner, size);
    this.symbol = grown(this.symbol, size);
    this.target = grown(this.target, size);
    this.next = grown(this.next, size);
    this.slots = new Int32Array(capacityFor(2 * size)).fill(-1);
    this.mask = this.slots.length - 1;
    for (let t = 0; t < this.count; t++) {
      this.slots[this.slotOf(this.owner[t] as number, this.symbol[t] as number)] = t;
    }
  }
}

// --- strings ------------------------------------------------------------------------------

/**
 * A set of strings that numbers its members 0, 1, 2, … in order of insertion
 * (the reference's line-interning `dict` and its `set`s of tag labels).
 */
export class StringTable {
  private keys: string[] = [];
  private hashes: Int32Array = new Int32Array(8);
  private slots = new Int32Array(16).fill(-1);
  private mask = 15;

  /** The number of members. */
  get size(): number {
    return this.keys.length;
  }

  private slotOf(s: string, h: number): number {
    const slots = this.slots;
    const mask = this.mask;
    let i = h & mask;
    for (;;) {
      const e = slots[i] as number;
      if (e < 0 || (this.hashes[e] === h && this.keys[e] === s)) return i;
      i = (i + 1) & mask;
    }
  }

  /** The number of `s`, or -1 when it is not a member. */
  get(s: string): number {
    return this.slots[this.slotOf(s, hashUnits(s, 0, s.length))] as number;
  }

  /** `s in table`. */
  has(s: string): boolean {
    return this.get(s) >= 0;
  }

  /** Add `s` when absent; returns its number. */
  add(s: string): number {
    const h = hashUnits(s, 0, s.length);
    const slot = this.slotOf(s, h);
    const found = this.slots[slot] as number;
    if (found >= 0) return found;
    const e = this.keys.length;
    this.keys.push(s);
    if (e === this.hashes.length) this.hashes = grown(this.hashes, 2 * e);
    this.hashes[e] = h;
    if (2 * (e + 1) > this.slots.length) {
      this.slots = new Int32Array(this.slots.length * 2).fill(-1);
      this.mask = this.slots.length - 1;
      for (let k = 0; k <= e; k++) this.slots[this.slotOf(this.keys[k] as string, this.hashes[k] as number)] = k;
    } else {
      this.slots[slot] = e;
    }
    return e;
  }
}

/**
 * The gram table of the reference's `_ParentStream`: for each word and each
 * pair of adjacent words (`"a b"`) of a word-stream, the offsets of its first
 * and last occurrence. A gram is stored as its first offset into the stream
 * with a flag for pairs, so no gram string is built.
 *
 * A gram is a run of the stream that starts at a word and ends where a word
 * ends (at a space or at the end of the stream), and the flag tells a word
 * from a pair that starts at the same offset; two grams are equal when their
 * flags and code units are.
 */
export class GramTable {
  private readonly stream: string;
  /** Per gram: first offset * 2 + (1 for a pair), last offset, hash. */
  private key: Int32Array;
  private lastAt: Int32Array;
  private hashes: Int32Array;
  private count = 0;
  private slots: Int32Array;
  private mask: number;

  constructor(stream: string, hint: number) {
    this.stream = stream;
    const size = Math.max(hint, 8);
    this.key = new Int32Array(size);
    this.lastAt = new Int32Array(size);
    this.hashes = new Int32Array(size);
    this.slots = new Int32Array(capacityFor(2 * size)).fill(-1);
    this.mask = this.slots.length - 1;
  }

  /** `text[from:to]` (a word, or a pair when `pair`) equals gram `g`. */
  private equals(g: number, text: string, from: number, to: number, pair: boolean): boolean {
    const key = this.key[g] as number;
    if ((key & 1) !== (pair ? 1 : 0)) return false;
    const stream = this.stream;
    const at = key >> 1;
    const end = at + (to - from);
    if (end > stream.length || (end < stream.length && stream.charCodeAt(end) !== 0x20)) return false;
    for (let i = 0; i < to - from; i++) {
      if (stream.charCodeAt(at + i) !== text.charCodeAt(from + i)) return false;
    }
    return true;
  }

  private slotOf(text: string, from: number, to: number, pair: boolean, h: number): number {
    const slots = this.slots;
    const mask = this.mask;
    let i = h & mask;
    for (;;) {
      const g = slots[i] as number;
      if (g < 0 || (this.hashes[g] === h && this.equals(g, text, from, to, pair))) return i;
      i = (i + 1) & mask;
    }
  }

  /** The gram equal to `text[from:to]` (a pair when `pair`), or -1. */
  find(text: string, from: number, to: number, pair: boolean): number {
    return this.slots[this.slotOf(text, from, to, pair, hashUnits(text, from, to))] as number;
  }

  /** The offset of the first occurrence of gram `g`. */
  first(g: number): number {
    return (this.key[g] as number) >> 1;
  }

  /** The offset of the last occurrence of gram `g`. */
  last(g: number): number {
    return this.lastAt[g] as number;
  }

  /**
   * Record an occurrence of the gram `stream[from:to]` (a pair when `pair`),
   * in increasing order of `from`; `h` is its {@link hashUnits}.
   */
  note(from: number, to: number, pair: boolean, h: number): void {
    const slot = this.slotOf(this.stream, from, to, pair, h);
    const found = this.slots[slot] as number;
    if (found >= 0) {
      this.lastAt[found] = from;
      return;
    }
    const g = this.count++;
    if (g === this.key.length) {
      const size = 2 * g;
      this.key = grown(this.key, size);
      this.lastAt = grown(this.lastAt, size);
      this.hashes = grown(this.hashes, size);
    }
    this.key[g] = from * 2 + (pair ? 1 : 0);
    this.lastAt[g] = from;
    this.hashes[g] = h;
    if (2 * this.count > this.slots.length) {
      this.slots = new Int32Array(this.slots.length * 2).fill(-1);
      this.mask = this.slots.length - 1;
      for (let k = 0; k < this.count; k++) {
        let i = (this.hashes[k] as number) & this.mask;
        while ((this.slots[i] as number) >= 0) i = (i + 1) & this.mask;
        this.slots[i] = k;
      }
    } else {
      this.slots[slot] = g;
    }
  }
}
