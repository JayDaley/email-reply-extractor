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

- `corpus.json` — the decoded bodies of the 26 hand-labeled fixture messages
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
- [conformance/README.md](conformance/README.md) — the conformance data.
- [fixtures/README.md](fixtures/README.md) — the fixture corpus and its
  provenance.
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup for each port and the
  change procedure.
- [CHANGELOG.md](CHANGELOG.md) — one section per release.

## License

MIT. See [LICENSE](LICENSE).
