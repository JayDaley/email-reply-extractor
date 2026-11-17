# email-reply-extractor

[![CI](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/JayDaley/email-reply-extractor/actions/workflows/ci.yml)

**Extract the new text from an email reply: strip quoted text, quote headers,
signatures and sign-offs.**

`email-reply-extractor` recovers the text an author newly wrote in an email,
in two stages:

- **Stage 1**, `extract_new_text`, removes quoted text (`>` lines, "On …
  wrote:" and other attribution lines, Outlook quote-header blocks in several
  languages, "Original message" dividers) from top-posted, bottom-posted and
  inline replies. It optionally uses the thread parent's body and the
  message's HTML part as evidence.
- **Stage 2**, `clean_for_scoring`, removes greetings, signatures, sign-offs,
  mailing-list footers and legal disclaimers from the stage-1 text, and
  reports which lines it removed. It optionally uses the sender's display
  name, the addressees' display names and the signature lines the sender
  repeats across messages (`learn_signature_lines`) as evidence.

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
described in [docs/extraction.md](docs/extraction.md). The figures are from
the origin application's corpus of 154,394 IETF list messages and from two
audits of messages drawn from it at random (see
[docs/generation-10.md](docs/generation-10.md)). On the held-out audit of 160
messages, 36 (22.5%) have at least one error in generation 10, against 104
(65%) in generation 9.

### Evidence the caller must supply

Several rules act only when the caller passes the evidence they need. Without
it, the corresponding errors remain.

- **The sender's name.** Without `author_name`, a trailing line that is only
  the sender's name is removed only as part of a salutation-and-name sign-off;
  a bare name with no salutation above it ("Dino", "—Daniel") is kept. In
  generation 9, 23.8% of the corpus's cleaned texts ended in a line that is
  only the sender's name. A sender whose display name yields no name token
  (an empty name, or a name with no letters) gets no sender-name rules, and a
  sender who signs in a different script from the display name is not
  matched.
- **Learned signature lines.** `learn_signature_lines` needs at least 5
  stage-1 texts of one sender, and a line must occur among the last 10
  non-blank lines of at least 3 of them and at least one fifth of them. A
  sender with fewer messages gets no learned lines.
- **The addressees' names.** Without `addressee_names`, a greeting line that
  only names people is removed only when it opens with a greeting word, or
  has one to four capitalized words ending in `,`, `:` or `-` ("Jane, Joe,").
- **The thread parent.** Without `parent_body`, a previous message reproduced
  with no `>` markers, no attribution line, no quote-header block and no
  sign-off boundary above it is not removed. Only the direct parent is
  compared: text pasted without markers from any other message is kept.
- **The HTML part.** The Apple Mail Share-sheet case, a flattened plain part,
  and a quoted message leaked unmarked into the plain part are recovered only
  when `html_body` is passed. The HTML assist removes nothing unless it
  removes at least 3 non-blank lines.
- **Decoding.** The functions take decoded strings. MIME parsing, charset
  decoding, resolving `In-Reply-To` to the parent's body, and storing a
  sender's earlier texts for `learn_signature_lines` are the caller's.

With none of the generation-10 evidence arguments, stage 2 still removes more
than generation 9 did: the tolerant sign-off forms and the greeting rules that
need no evidence change the cleaned text of 19,910 of the 154,394 messages.

### What remains

- **Author text carrying `>` markers.** A pasted diff, an mbox `>From` line,
  or inline replies typed inside an Outlook quote block carry quote markers
  and are removed as quoted text: 9 of the 160 held-out messages.
- **Role lines and split signature blocks.** A role line written above the
  sign-off ("Speaking as IAB Chair,", "(as IAB Chair)") is kept, and so is a
  signature block or role line separated from the sign-off by a blank line,
  when it has no `-- ` delimiter, no recognizable contact shape and no learned
  or HTML-hinted lines. A role written on the name line ("Jane (as chair)",
  "Jane, as IAB Chair") or directly below it is removed with the name.
- **Long wrap remainders.** A re-wrapped quote remainder between two quote
  lines is removed only when it has at most two words and starts lowercase;
  a longer remainder is kept as the author's.
- **Text the author repeats from the thread parent.** The parent-diff assist
  cannot always tell a quotation the client left unmarked from a line the
  author wrote again. A repeated line is kept when the message also carries it
  inside its own `>`-marked quotation and the line sits in a block that is
  mostly unmatched text (generation 9), and when the client marks its
  quotation and the line stands in a block with no quote line (marking mode,
  generation 10). A message whose client marks no quotation loses such lines;
  a paragraph a revision carries over unchanged from the parent is removed as
  a whole; and a message that answers an unmarked copy of the parent point by
  point in 8 or more pieces loses its protected runs.
- **Short quotations without markers.** An unmarked quoted line of fewer than
  8 words is removed only as part of a matching run of at least 10 words, or
  by chaining from a removed neighbour when at least 3 lines were removed. A
  short quoted line that stands alone is kept.
- **Part boundaries.** `part_starts` is empty (one part) when a line of the
  text has no counterpart in the body: 10 of the 154,394 messages (72 in
  generation 9).

### Languages and clients

- **Attribution lines** are recognized in English, German, French, Spanish
  and Japanese fixed forms. Other wordings are recognized only when they end
  in a colon directly above a quote line and carry an e-mail address, a clock
  time and a date, or a speech verb from the list in
  [docs/extraction.md](docs/extraction.md).
- **Quote-header blocks and "Original message" dividers** are recognized with
  English, German, French, Spanish, Portuguese, Italian, Dutch and Chinese
  labels, and the short "Original" dividers of QQ Mail and Foxmail. A
  localized block is recognized only when its opener is followed by at least
  two further field lines including the language's subject label.
- **Greetings and sign-offs** are matched against fixed multilingual lists,
  and, with the evidence, against the sender's and addressees' names. A
  salutation outside the lists is kept.
- **HTML quote containers** are `<blockquote>`, the Gmail, Mozilla and Outlook
  class and id hooks listed in [docs/extraction.md](docs/extraction.md), and Apple Mail's
  Share-sheet wrapper. A client that marks quoted text by another means is not
  recognized, and its quoted text counts as the author's.

### Structure the rules require

- **Sign-off boundary.** A fully top-posted reply with no `>` markers is cut
  at its sign-off only when a salutation line, a name line and an attribution
  follow one another. A reply with no sign-off, or no attribution after it, is
  not cut there.
- **Pasted headers.** A `From:` block the author pasted as evidence is kept
  only when a transport or trace field (`Message-ID`, `Received`, …) sits in
  the header run directly above it; otherwise the body is truncated at the
  block.

### Scope of the measurements

The hand-labeled corpus is 28 messages (25 from the public IETF mail archive,
3 synthetic), processed without the evidence arguments; the rules that need
evidence are pinned by unit tests and conformance vectors only. All measured
effects recorded in `docs/` are taken over IETF mailing-list mail. Behaviour
on other kinds of mail (personal correspondence, customer-support threads,
newsletters) has not been measured. No minimum-length gate is applied:
whether a short extraction is worth processing is the consuming application's
decision.

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
- [docs/generation-5.md](docs/generation-5.md) — generation 5 and the
  scanner changes deferred to generation 10.
- [docs/generation-7.md](docs/generation-7.md) — generation 7: the linear
  parent-diff assist and its new aligned-run rule.
- [docs/generation-8.md](docs/generation-8.md) — generation 8: Apple Mail
  Share-sheet containers.
- [docs/generation-9.md](docs/generation-9.md) — generation 9: the
  parent-diff own-quotation rule.
- [docs/generation-10.md](docs/generation-10.md) — generation 10: the
  scanner removed, new stage-1 rules, and evidence for stage 2.
- [conformance/README.md](conformance/README.md) — the conformance data.
- [fixtures/README.md](fixtures/README.md) — the fixture corpus and its
  provenance.
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup for each port and the
  change procedure.
- [CHANGELOG.md](CHANGELOG.md) — one section per release.

## License

MIT. See [LICENSE](LICENSE).
