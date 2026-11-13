/**
 * The part of Python's `difflib.SequenceMatcher` the reference uses:
 * `SequenceMatcher(a=…, b=…, autojunk=False)` with no junk function, and its
 * `get_matching_blocks()`. Ported literally from CPython 3.14, so the matching
 * blocks (and their tie-breaking) are identical.
 *
 * @internal
 */

/** A matching block: `a[a:a+size] == b[b:b+size]`. */
export type MatchBlock = readonly [a: number, b: number, size: number];

export class SequenceMatcher {
  private readonly a: readonly string[];
  private readonly b: readonly string[];
  private readonly b2j: Map<string, number[]>;

  constructor(a: readonly string[], b: readonly string[]) {
    this.a = a;
    this.b = b;
    // __chain_b with isjunk=None and autojunk=False: no junk, no popular set.
    this.b2j = new Map();
    b.forEach((elt, i) => {
      const indices = this.b2j.get(elt);
      if (indices) indices.push(i);
      else this.b2j.set(elt, [i]);
    });
  }

  findLongestMatch(alo: number, ahi: number, blo: number, bhi: number): MatchBlock {
    const { a, b, b2j } = this;
    let besti = alo;
    let bestj = blo;
    let bestsize = 0;
    let j2len = new Map<number, number>();
    const nothing: number[] = [];
    for (let i = alo; i < ahi; i++) {
      const newj2len = new Map<number, number>();
      for (const j of b2j.get(a[i] as string) ?? nothing) {
        if (j < blo) continue;
        if (j >= bhi) break;
        const k = (j2len.get(j - 1) ?? 0) + 1;
        newj2len.set(j, k);
        if (k > bestsize) {
          besti = i - k + 1;
          bestj = j - k + 1;
          bestsize = k;
        }
      }
      j2len = newj2len;
    }
    // With no junk, only the non-junk extension loops can move the block.
    while (besti > alo && bestj > blo && a[besti - 1] === b[bestj - 1]) {
      besti--;
      bestj--;
      bestsize++;
    }
    while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] === b[bestj + bestsize]) {
      bestsize++;
    }
    return [besti, bestj, bestsize];
  }

  getMatchingBlocks(): MatchBlock[] {
    const la = this.a.length;
    const lb = this.b.length;
    const queue: [number, number, number, number][] = [[0, la, 0, lb]];
    const matchingBlocks: MatchBlock[] = [];
    while (queue.length > 0) {
      const [alo, ahi, blo, bhi] = queue.pop() as [number, number, number, number];
      const x = this.findLongestMatch(alo, ahi, blo, bhi);
      const [i, j, k] = x;
      if (k) {
        matchingBlocks.push(x);
        if (alo < i && blo < j) queue.push([alo, i, blo, j]);
        if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
      }
    }
    matchingBlocks.sort((p, q) => p[0] - q[0] || p[1] - q[1] || p[2] - q[2]);
    let i1 = 0;
    let j1 = 0;
    let k1 = 0;
    const nonAdjacent: MatchBlock[] = [];
    for (const [i2, j2, k2] of matchingBlocks) {
      if (i1 + k1 === i2 && j1 + k1 === j2) {
        k1 += k2;
      } else {
        if (k1) nonAdjacent.push([i1, j1, k1]);
        i1 = i2;
        j1 = j2;
        k1 = k2;
      }
    }
    if (k1) nonAdjacent.push([i1, j1, k1]);
    nonAdjacent.push([la, lb, 0]);
    return nonAdjacent;
  }
}
