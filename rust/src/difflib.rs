//! The part of Python's `difflib.SequenceMatcher` the reference uses.
//!
//! `strip_parent_content` runs `SequenceMatcher(a=child, b=parent,
//! autojunk=False)` with no junk function and reads `get_matching_blocks()`.
//! The blocks depend on the algorithm's tie-breaking, so this is a literal
//! port of `find_longest_match` and `get_matching_blocks` (CPython 3.14),
//! specialised to "no junk, no popularity pruning".

use std::collections::HashMap;

/// A matching block `a[a..a+size] == b[b..b+size]`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct Match {
    pub(crate) a: usize,
    pub(crate) b: usize,
    pub(crate) size: usize,
}

struct Matcher {
    a: Vec<usize>,
    b: Vec<usize>,
    /// For each element id, the indices where it occurs in `b`, ascending.
    b2j: HashMap<usize, Vec<usize>>,
}

impl Matcher {
    fn find_longest_match(&self, alo: usize, ahi: usize, blo: usize, bhi: usize) -> Match {
        let (a, b) = (&self.a, &self.b);
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0usize);
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        let nothing: Vec<usize> = Vec::new();
        for (i, elt) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut newj2len: HashMap<usize, usize> = HashMap::new();
            for &j in self.b2j.get(elt).unwrap_or(&nothing) {
                if j < blo {
                    continue;
                }
                if j >= bhi {
                    break;
                }
                let prev = if j == 0 {
                    0
                } else {
                    j2len.get(&(j - 1)).copied().unwrap_or(0)
                };
                let k = prev + 1;
                newj2len.insert(j, k);
                if k > bestsize {
                    besti = i + 1 - k;
                    bestj = j + 1 - k;
                    bestsize = k;
                }
            }
            j2len = newj2len;
        }
        // With no junk, only the non-junk extension loops can apply.
        while besti > alo && bestj > blo && a[besti - 1] == b[bestj - 1] {
            besti -= 1;
            bestj -= 1;
            bestsize += 1;
        }
        while besti + bestsize < ahi
            && bestj + bestsize < bhi
            && a[besti + bestsize] == b[bestj + bestsize]
        {
            bestsize += 1;
        }
        Match {
            a: besti,
            b: bestj,
            size: bestsize,
        }
    }

    fn get_matching_blocks(&self) -> Vec<Match> {
        let (la, lb) = (self.a.len(), self.b.len());
        let mut queue = vec![(0, la, 0, lb)];
        let mut blocks: Vec<Match> = Vec::new();
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let x = self.find_longest_match(alo, ahi, blo, bhi);
            let (i, j, k) = (x.a, x.b, x.size);
            if k > 0 {
                blocks.push(x);
                if alo < i && blo < j {
                    queue.push((alo, i, blo, j));
                }
                if i + k < ahi && j + k < bhi {
                    queue.push((i + k, ahi, j + k, bhi));
                }
            }
        }
        blocks.sort();
        let (mut i1, mut j1, mut k1) = (0, 0, 0);
        let mut non_adjacent = Vec::new();
        for m in blocks {
            if i1 + k1 == m.a && j1 + k1 == m.b {
                k1 += m.size;
            } else {
                if k1 > 0 {
                    non_adjacent.push(Match {
                        a: i1,
                        b: j1,
                        size: k1,
                    });
                }
                (i1, j1, k1) = (m.a, m.b, m.size);
            }
        }
        if k1 > 0 {
            non_adjacent.push(Match {
                a: i1,
                b: j1,
                size: k1,
            });
        }
        non_adjacent.push(Match {
            a: la,
            b: lb,
            size: 0,
        });
        non_adjacent
    }
}

/// `SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()` over
/// two sequences of strings compared by equality.
pub(crate) fn matching_blocks(a: &[String], b: &[String]) -> Vec<Match> {
    // Interning keeps element comparison cheap; ids are only compared for
    // equality, so the numbering itself is irrelevant.
    let mut ids: HashMap<&str, usize> = HashMap::new();
    let mut a_ids = Vec::with_capacity(a.len());
    for s in a {
        let next = ids.len();
        a_ids.push(*ids.entry(s.as_str()).or_insert(next));
    }
    let mut b_ids = Vec::with_capacity(b.len());
    for s in b {
        let next = ids.len();
        b_ids.push(*ids.entry(s.as_str()).or_insert(next));
    }
    let mut b2j: HashMap<usize, Vec<usize>> = HashMap::new();
    for (j, &id) in b_ids.iter().enumerate() {
        b2j.entry(id).or_default().push(j);
    }
    Matcher {
        a: a_ids,
        b: b_ids,
        b2j,
    }
    .get_matching_blocks()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &str) -> Vec<String> {
        s.chars().map(|c| c.to_string()).collect()
    }

    #[test]
    fn docstring_examples() {
        let blocks = matching_blocks(&v("abxcd"), &v("abcd"));
        let got: Vec<(usize, usize, usize)> = blocks.iter().map(|m| (m.a, m.b, m.size)).collect();
        assert_eq!(got, vec![(0, 0, 2), (3, 2, 2), (5, 4, 0)]);
        let blocks = matching_blocks(&v(" abcd"), &v("abcd abcd"));
        assert_eq!((blocks[0].a, blocks[0].b, blocks[0].size), (0, 4, 5));
    }
}
