# email-reply-extractor

[![CI](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml)

**Extract the new text from an email reply: strip quoted text, quote headers,
signatures and sign-offs.**

`email-reply-extractor` recovers the text an author newly wrote in an email,
in two stages:

- **Stage 1**, `extract_new_text`, removes quoted text (`>` lines, "On …
  wrote:" attribution lines, Outlook quote-header blocks in several languages,
  "Original message" dividers) from top-posted, bottom-posted and inline
  replies. It optionally uses the thread parent's body and the message's HTML
  part as evidence.
- **Stage 2**, `clean_for_scoring`, removes greetings, signatures, sign-offs,
  mailing-list footers and legal disclaimers from the stage-1 text, and
  reports which lines it removed.

Both stages take already-decoded strings and have no runtime dependencies.
`EXTRACTION_VERSION`, an integer, identifies the generation of the routine,
so applications that store derived text can tell when it needs re-deriving
([docs/versioning.md](docs/versioning.md)).

## Ports

The library is maintained in three languages in this repository. All three
carry the same version and `EXTRACTION_VERSION` and derive byte-identical text
from the same input.

| Language | Package | Install | Documentation |
|---|---|---|---|
| Python (reference implementation) | [`email-reply-extractor`](https://pypi.org/project/email-reply-extractor/) on PyPI | `pip install email-reply-extractor` | [python/README.md](python/README.md) |
| TypeScript | `email-reply-extractor` on npm | `npm install email-reply-extractor` | [typescript/README.md](typescript/README.md) |
| Rust | `email-reply-extractor` on crates.io | `cargo add email-reply-extractor` | [rust/README.md](rust/README.md) |

The Python package defines the behaviour; the TypeScript and Rust ports
reproduce it. [docs/ports.md](docs/ports.md) is the contract between them.

## Conformance

`conformance/` holds language-neutral test data generated from the Python
implementation:

- `corpus.json` — the decoded bodies of the 28 hand-labeled fixture messages
  in `fixtures/`, with the reference output of every public function for each;
- `vectors/` — call-level vectors (arguments and result) recorded while the
  Python unit tests run, one file per function;
- `digest.json` — a SHA-256 over the corpus output, recorded against the
  current `EXTRACTION_VERSION`.

Each port's test suite replays the vectors, reproduces the corpus outputs, and
computes the digest. A behaviour change is made in Python first, the data is
regenerated with `make vectors`, and the ports are changed until they pass.
The formats and the digest algorithm are specified in
[conformance/README.md](conformance/README.md).

## Limitations

The rules are heuristics over text, tuned and measured on technical
mailing-list mail. The limits below are known; each follows from a rule
described in [docs/extraction.md](docs/extraction.md).

### Text the author repeats from the thread parent

The parent-diff assist removes lines of a reply that also occur in the thread
parent. It cannot always tell a quotation the client left unmarked from a line
the author wrote again. From generation 9 a repeated line is kept when the
message also carries it inside its own `>`-marked quotation and the line sits
in a block of consecutive non-blank lines that is mostly unmatched text (see
[docs/generation-9.md](docs/generation-9.md)). Outside those conditions:

- **A message with no marked quotation.** When the client marks no quotation
  (an Outlook-style reply, a plain proposed-text edit), a line the author
  repeats from the parent is removed: a line kept in a revised paragraph, the
  new wording of an `OLD:` / `NEW:` edit, a phrase of the parent cited inside
  a sentence.
- **Passages left unchanged.** A paragraph or list item that a revised
  document carries over unchanged from the version in the parent is removed
  as a whole.
- **Short blocks.** A repeated line that has at least as many words as the
  rest of its block is removed, for example the first line of a two-line list
  item whose second line was reworded.
- **A quoted line directly above its answer.** When an author pastes a line of
  the parent and answers it on the next line, with no blank line between, and
  the answer is longer than the pasted line, the pasted line is kept if the
  message also quotes it with markers.
- **Short quotations without markers.** An unmarked quoted line of fewer than
  8 words is removed only as part of a matching run of at least 10 words, or
  by chaining from a removed neighbour when at least 3 lines were removed. A
  short quoted line that stands alone is kept.

### Evidence the caller must supply

- **The thread parent.** Without `parent_body`, a previous message reproduced
  with no `>` markers, no attribution line, no quote-header block and no
  sign-off boundary above it is not removed. Only the direct parent is
  compared: text pasted without markers from any other message is kept.
- **The HTML part.** The Apple Mail Share-sheet case, a flattened plain part,
  and a quoted message leaked unmarked into the plain part are recovered only
  when `html_body` is passed. The HTML assist removes nothing unless it
  removes at least 3 non-blank lines.
- **Decoding.** The functions take decoded strings. MIME parsing, charset
  decoding and resolving `In-Reply-To` to the parent's body are the caller's.

### Languages and clients

- **Attribution lines** are recognized in English, German, French, Spanish
  and Japanese forms. An attribution line in another language is kept as text.
- **Quote-header blocks and "Original message" dividers** are recognized with
  English, German, French, Spanish, Portuguese, Italian, Dutch and Chinese
  labels. A localized block is recognized only when its opener is followed by
  at least two further field lines including the language's subject label.
- **Greetings and sign-offs** are matched against fixed multilingual lists; a
  salutation outside them is kept.
- **HTML quote containers** are `<blockquote>`, the Gmail, Mozilla and Outlook
  class and id hooks listed in [docs/extraction.md](docs/extraction.md), and Apple Mail's
  Share-sheet wrapper. A client that marks quoted text by another means is not
  recognized, and its quoted text counts as the author's.

### Structure the rules require

- **Sign-off boundary.** A fully top-posted reply with no `>` markers is cut
  at its sign-off only when a salutation line, a name line and an attribution
  line follow one another. A reply with no sign-off, or no attribution line
  after it, is not cut there.
- **Pasted headers.** A `From:` block the author pasted as evidence is kept
  only when a transport or trace field (`Message-ID`, `Received`, …) sits in
  the header run directly above it; otherwise the body is truncated at the
  block.
- **Part boundaries.** `part_starts` is empty (one part) when a line of the
  text has no counterpart in the body, which happens when the scanner joined
  wrapped lines into one: 11 of 29,895 sampled extractions.

### What stage 2 keeps

Stage 2 removes one opening greeting line, one closing sign-off and trailing
disclaimer paragraphs. It keeps a bare trailing name with no salutation
("Dino", "—Daniel"), greetings and valedictions in the middle of the text,
elision markers (`[...]`), URLs pasted in prose, and a disclaimer-shaped
paragraph that is not at the end. Signature lines with no `-- ` delimiter and
no recognizable contact shape are removed only with the HTML signature hint.

### Scope of the measurements

The hand-labeled corpus is 28 messages (25 from the public IETF mail archive,
3 synthetic), and the measured effects recorded in `docs/` are taken over the
stored IETF mailing-list mail of the origin application. The corpus digest
covers no fixture that exercises the generation-9 rule, which is pinned by
unit tests and conformance vectors only. Behaviour on other kinds of mail
(personal correspondence, customer-support threads, newsletters) has not been
measured. No minimum-length gate is applied: whether a short extraction is
worth processing is the consuming application's decision.

## Repository layout

| Path | Content |
|---|---|
| `python/` | The Python package (reference implementation) |
| `typescript/` | The TypeScript package |
| `rust/` | The Rust crate |
| `fixtures/` | Hand-labeled corpus of real mailing-list messages |
| `conformance/` | Generated, committed test data shared by the ports |
| `docs/` | The extraction rules, the versioning contract, the port contract |

## Documentation

- [docs/extraction.md](docs/extraction.md) — the rules of both stages.
- [docs/versioning.md](docs/versioning.md) — the `EXTRACTION_VERSION`
  contract.
- [docs/ports.md](docs/ports.md) — the port contract and the semantic details
  a port must reproduce.
- [docs/generation-5.md](docs/generation-5.md) — generation 5 and the deferred
  scanner changes.
- [docs/generation-7.md](docs/generation-7.md) — generation 7: the linear
  parent-diff assist and its new aligned-run rule.
- [docs/generation-8.md](docs/generation-8.md) — generation 8: Apple Mail
  Share-sheet containers.
- [docs/generation-9.md](docs/generation-9.md) — generation 9: the
  parent-diff own-quotation rule.
- [conformance/README.md](conformance/README.md) — the conformance data.
- [fixtures/README.md](fixtures/README.md) — the fixture corpus and its
  provenance.
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup for each port and the
  change procedure.
- [CHANGELOG.md](CHANGELOG.md) — one section per release.

## License

MIT. See [LICENSE](LICENSE).
