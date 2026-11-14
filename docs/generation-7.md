# Generation 7

Shipped in v1.3.0. The parent-diff assist (`strip_parent_content`) runs in
time linear in the size of its input; its aligned-run rule is replaced, which
changes the derived text of some messages. The fragment scanner inserts the
collapsed attribution header literally.

## The aligned-run rule: alignment around the longest common run

Through generation 6 the aligned-run rule (rule (a) of the parent-diff
assist) took the matching blocks of `difflib.SequenceMatcher` over the child's
and the parent's normalized non-blank lines. `SequenceMatcher` is quadratic in
the number of lines: 40,000 equal lines took more than 90 seconds. The input
is untrusted, so generation 7 replaces it with an alignment that runs in
linear time.

The rule, over the child's lines c[0..N) and the parent's lines p[0..M),
compared by exact string equality:

1. A *common run* is a child interval of at least one line that occurs
   contiguously in the parent. K is the greatest length, in lines, of a
   common run; when K is 0 nothing is marked. The *anchor* is the common run
   of length K with the smallest start i in the child; its parent position j
   is the smallest start of an occurrence of that run in the parent.
2. The anchor's lines are marked when it has substance: at least 10 words in
   total, or one line of at least 8 words.
3. A line of c[0..i) is marked when it lies in a run of c[0..i) that has
   substance and occurs contiguously in p[0..j). A line of c[i+K..N) is
   marked likewise against p[j+K..M). Within a side, runs match in any order.

The anchor is normally the quoted previous message. Splitting at it keeps a
block the author repeated on one side of the quote (a sign-off, a contact
block) from matching the parent's copy on the other side. Within a side, any
number of child copies of a block match the parent's copy, in any order;
`difflib` matched each parent line at most once and only in order.

The implementation uses a suffix automaton over parent line ids
(`_build_automaton`, `_walk`, `_cover_side`, `_aligned_run_marks` in
`extraction.py`); a brute-force transcription of the specification is
compared with it in `python/tests/test_parent_diff.py`, and the tie-breaks are
recorded as conformance vectors.

### Measured effect

Over the 179,566 messages stored by the origin application, each extracted
with its thread parent and HTML part as the application resolves them:

- 34 messages change: the stage-1 text of 26, the cleaned (stage-2) text of
  13 (all 13 in substance, by `texts_equivalent`), the method of 11, the
  status of none.
- The method changes are 10 `erp+custom+parent-diff+html-quote` to
  `erp+custom+parent-diff` (the parent-diff assist now removes the lines the
  HTML quoted-text oracle removed before) and 1 in the opposite direction.
- In the changed stage-1 texts, generation 7 keeps fewer lines in 24 and more
  in 2. The removed lines are mostly further copies of quoted material that
  generation 6 matched once: list footers, signature blocks of third parties,
  reproduced agenda items and reproduced draft text. Some are lines the
  author wrote that also occur in the quoted text on the same side of the
  anchor.
- A blind side-by-side review of the 34 changed outputs judged the cleaned
  text better under generation 7 in 7 messages, better under generation 6 in
  6, and equal in 21.

The fixture `toppost-repeated-signature-synthetic-01` and the test
`test_parent_diff_removes_a_reproduced_block_out_of_order` reproduce the
shapes in which generation 7 removes further quoted copies.

## The rewrap and continuation rules: an index over the parent

Rules (b) and (c) test whether a child line, or two adjacent child lines
joined by a space, occur in the parent's word-stream (the parent's
normalized lines joined by single spaces). Through generation 6 each test
searched the stream, so a long child against a long parent took quadratic
time. Generation 7 answers the same tests from an index built once per call
(`_ParentStream`):

- A table of the stream's words and adjacent word pairs, with the offsets of
  their first and last occurrence, decides most queries: the interior words
  of a needle are whole words of the stream at fixed distances from the
  needle's start, so an absent word or pair proves the needle absent, a word
  or pair that occurs once fixes the only possible start, and a match at the
  first occurrence proves it present.
- Every other query walks a suffix automaton of the stream's characters,
  built on the first query that needs it.

The answers are those of the substring search. Over the stored messages the
outputs of the two are identical, and a randomized comparison over 7.8 million
queries found no difference.

## The inline-reply author-tag check

The check that a tagged line below a localized quote header is the author's
searched the parent body and the text above the header once per tagged line.
Generation 7 tests all tags together, in one scan of each text for every tag
length present (tags are at most 20 characters). The answers are unchanged.

## The fragment scanner: literal header insertion

Through generation 6 the vendored scanner passed the collapsed "On … wrote:"
header to `re.sub` as a replacement template: backslash sequences in a body
were rewritten, an invalid escape failed the extraction, and repeated `\1`
expanded the output quadratically. The collapsed header is now inserted
literally. Output changes only for a body whose collapsed header contains a
backslash; over the stored messages, none changed.

## Running time

Measured with the Python reference (CPython 3.14, one process) on the
adversarial shapes that take quadratic time in generation 6. The child has
the stated number of lines and the parent about as many; times are for one
call with `continuation=True` (`strip_parent_content`) or with the parent
supplied (`extract_new_text`), and memory is the growth of the process's peak
resident size during the `strip_parent_content` call.

| Shape | Generation 6, 20,000 lines | Generation 7, 1,000,000 lines | `extract_new_text` | Memory |
|---|---:|---:|---:|---:|
| 30 random words per line, no common line | 29.3 s | 42.8 s | 77.9 s | 4.7 GB |
| the parent with every other line changed | 11.5 s | 6.2 s | 15.3 s | 1.6 GB |
| the parent repeated twice | 0.4 s | 3.5 s | 13.0 s | 1.0 GB |
| distinct lines each 2-3 times, shuffled independently | 1.1 s | 8.4 s | 17.5 s | 1.3 GB |
| one shared line alternating with distinct lines | more than 120 s | 5.8 s | 13.3 s | 1.2 GB |
| re-wrapped paragraph with 3-word remainder lines | 1.6 s | 5.2 s | 11.7 s | 1.0 GB |
| rewrap queries that only the suffix automaton decides | not measured | 3.3 s | 9.0 s | 1.1 GB |

Generation 6 grows quadratically: at 10,000 lines the first shape took 7.3 s,
at 20,000 lines 29.3 s. Every shape was measured in generation 7 at 10,000,
20,000, 40,000, 80,000, 200,000, 500,000 and 1,000,000 lines; time and
memory grow roughly in proportion to the input (from 500,000 to 1,000,000
lines the time grows by a factor of 1.7 to 2.7). The largest input (the first
shape at 1,000,000 lines) is 236 MB of child text and as much parent text.

On ordinary mail the index costs time that the substring search did not.
Over the 181,719 `strip_parent_content` calls the pipeline makes on the
179,566 stored messages, the summed time of `strip_parent_content` is 3.5
times that of generation 6 (139 s against 40 s), and the summed time of
`extract_new_text` is 1.28 times (484 s against 379 s). The suffix automaton
was built in 4,258 of the 181,719 calls; in Python it takes about 350 to 500
bytes of memory per parent character.
