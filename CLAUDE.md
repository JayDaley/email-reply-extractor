# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

`email-reply-extractor` — a library that recovers the author's newly written
text from an email in two stages, maintained as three ports in one
repository:

- **Python** (`python/`) — the reference implementation, on PyPI. Pure
  standard library, no runtime dependencies, Python >= 3.10.
- **TypeScript** (`typescript/`) — a port, on npm.
- **Rust** (`rust/`) — a port, on crates.io.

The three share one package version, one `EXTRACTION_VERSION`, one
`CHANGELOG.md`, and one language-neutral conformance suite (`conformance/`).
The contract between them is `docs/ports.md`.

## The three-port rule

Python is the reference implementation. A change to extraction behaviour is
made in Python first (with its unit tests), `make vectors` regenerates
`conformance/`, and the TypeScript and Rust ports are then changed until they
pass. **A change to extraction behaviour is incomplete until all three ports
pass the regenerated conformance suite**, and all three land in the same
commit and release. Never edit the JSON under `conformance/` by hand.

## Layout

- `README.md` — overview of the project and the three ports
- `python/` — the reference implementation
  - `pyproject.toml`, `README.md` (the PyPI readme), `LICENSE` (a copy of the
    root `LICENSE`; setuptools cannot reach outside `python/`)
  - `src/email_reply_extractor/` — package source (src layout)
    - `__init__.py` — the public API and `__version__`
    - `extraction.py` — stage 1 (`extract_new_text`) and `EXTRACTION_VERSION`
    - `cleaning.py` — stage 2 (`clean_for_scoring`)
    - `html_text.py` — `html_to_text` / `split_html_parts`, the HTML oracle
    - `equivalence.py` — `tolerant_lines` / `texts_equivalent`
    - `_fragments.py` — the vendored email-reply-parser fragment scanner
  - `tests/` — pytest suite: unit tests, `corpus.py` (decodes the fixtures),
    `conformance_support.py` (the conformance file formats),
    `test_conformance.py` (committed data matches live output; vector replay),
    `test_extraction_version.py` (the digest contract), `test_sync.py`,
    `test_port_hazards.py` (pins Python-specific semantics for the ports)
  - `tools/conformance.py` — generates and checks `conformance/`
  - `tools/check_sync.py` — version, `EXTRACTION_VERSION` and `LICENSE` sync
    across ports
- `typescript/` — the TypeScript port (`package.json`, `src/extraction.ts`
  holds `EXTRACTION_VERSION`, `LICENSE` copy)
- `rust/` — the Rust port (`Cargo.toml`, `src/extraction.rs` holds
  `EXTRACTION_VERSION`, `LICENSE` copy)
- `fixtures/` — the hand-labeled corpus of 26 real messages (`<stem>.eml`,
  `expected/<stem>.txt`, `README.md`), shared by all ports
- `conformance/` — generated, committed test data: `corpus.json`,
  `digest.json` (the single recorded corpus digest), `vectors/`; its
  `README.md` specifies the formats and the digest algorithm
- `docs/` — `extraction.md` (the rules of both stages), `versioning.md` (the
  `EXTRACTION_VERSION` contract), `ports.md` (the port contract and the
  semantic hazards a porter must respect), `generation-5.md` (planned output
  changes)
- `Makefile` — run from the root: `make test`, `make lint`, `make vectors`,
  `make check-vectors`, `make check-sync`, and per-port
  `make test-python|test-typescript|test-rust` and `lint-…` targets
- `.github/workflows/` — `ci.yml` (python, conformance, typescript and rust
  jobs), `publish.yml` (PyPI), `publish-npm.yml`, `publish-crates.yml`
- `CHANGELOG.md` — one section per release of all three ports (see Changelog
  below)
- `CONTRIBUTING.md` — development setup per port and the change procedure

The module docstrings in the Python source modules are the authoritative
design documentation; `docs/` restructures them for readers. Keep the two in
step: a rule change that touches a docstring touches `docs/extraction.md` as
well.

## Documentation style — hard rule

All documentation (`README.md`, every port's `README.md`, everything under
`docs/`, `conformance/README.md`, `fixtures/README.md`, `CONTRIBUTING.md`,
and any other published prose) must be written in a simple, factual,
impersonal style. No editorializing: no opinionated flourishes,
colloquialisms, first-person voice, or subjective commentary — state facts,
measurements and rationale plainly.

## Extraction version

`EXTRACTION_VERSION` (an integer, currently 6) identifies the generation of
the routine that derives an extraction's text: `extraction.py`, `cleaning.py`,
`html_text.py` and `_fragments.py` taken together, and their counterparts in
the ports. It is independent of the package version and is incremented
separately. It is declared in `python/src/email_reply_extractor/extraction.py`,
`typescript/src/extraction.ts` and `rust/src/extraction.rs`, and recorded with
the corpus digest in `conformance/digest.json`; `make check-sync` requires all
of them to agree.

- Increment it by one, by hand, in every port, in the same commit as any
  change to those modules that could alter the extracted text, the cleaned
  text, or an extraction's status — a whitespace-only difference included. Do
  not increment it for comments, docstrings, type annotations or refactors
  that keep every output byte identical.
- It only ever increases. Consumers compare a stored stamp with the running
  value using `<`, so an older consumer opening a store written by a newer
  routine reads that store as current instead of offering to re-derive text it
  cannot reproduce.
- `conformance/digest.json` pins the routine's output over the fixture corpus
  as a single SHA-256 and is the only place the digest is recorded. After an
  increment, `make vectors` re-records it together with its
  `extraction_version`. `python/tests/test_extraction_version.py` fails when
  the output moves without a re-record, and when the recorded generation and
  the constant differ; `make vectors` refuses to re-record a moved digest
  without an increment.
- Adding or removing a fixture moves the digest without changing the routine:
  `make vectors` re-records the digest and leaves the generation where it is.
  An edited `.eml` needs `python python/tools/conformance.py --rerecord-digest`.
- Six generations exist: **1** (initial release of the origin application),
  **2** (from its v1.2.0), **3** (from v1.11.0), **4** (from v1.15.0, the
  generation this package's v1.0.0 shipped), **5** (this package's v1.1.0:
  fragment edges kept, Outlook-boundary fix uncapped) and **6** (v1.2.0:
  Latin-script localized quote-header blocks and dividers). The digest of each
  is listed in `conformance/README.md`.

## The vendored scanner

`_fragments.py` is trimmed from `email-reply-parser` 0.5.12. Two upstream
defects (a `count=8` cap on the Outlook-boundary fix; per-fragment edge
stripping) were preserved through generation 4 and fixed in generation 5 —
see `docs/generation-5.md`. One upstream behavior remains and is deliberate:
the wrapped-attribution collapse, including its template-substitution failure
mode (pinned in `python/tests/test_port_hazards.py`; the ports reproduce it).
`extraction.py`'s quote-header pre-truncation compensates for the collapse's
line-gluing and must stay until the collapse itself is removed; that removal
is deferred, with the measured evidence, in `docs/generation-5.md`. Any change
to the scanner that can move output bytes is a generation bump with a digest
re-record.

## Versioning

The packages use [semantic versioning](https://semver.org/); the current
version is **1.2.1**. The three ports always carry the same version. The
sources are `__version__` in `python/src/email_reply_extractor/__init__.py`
(`python/pyproject.toml` reads it dynamically), `version` in
`typescript/package.json`, and `version` in `rust/Cargo.toml`; `make
check-sync` (run by CI and by the Python suite) fails when they differ.

Bump policy — the component to bump is decided by whether the change affects
the text consumers derive, not by how large or how visible it is:

- **major** — a breaking change to the public API of any port: a removed or
  renamed export, a changed signature, a changed return shape.
- **minor** — any change that affects the derived text, which is exactly any
  change that bumps `EXTRACTION_VERSION`. A purely additive API change (a new
  export, a new optional argument) is a minor bump too.
- **patch** — every other change, however large. A bug fix that provably keeps
  every output byte identical, an internal refactor, a documentation or test
  change, or a packaging change is a patch.

`EXTRACTION_VERSION` is not tied to the package version; it carries its own
number and its own contract (see above and `docs/versioning.md`).

## Changelog — maintain it

`CHANGELOG.md` records every release, newest first, and is parsed by scripts.
Its own "Format" section is the specification; keep to it exactly. One
changelog covers all three ports.

- Every version bump gets a section. Bumping the version without adding or
  updating a `CHANGELOG.md` section is incomplete work.
- Section shape, in this order: `## [<version>] - <date>`, then one
  `Summary: <one line>` line, then one `- ` bullet per individual change.
- `<date>` is `YYYY-MM-DD` for a committed release, or the literal
  `unreleased` while the version is bumped in the source but not yet
  committed. Replace `unreleased` with the commit date when releasing.
- One line per bullet — no wrapped continuation lines, no nested bullets, no
  extra headings or prose inside a release section.
- Write bullets at the granularity of an individual change (a new rule, a new
  export, a fixed defect), not per file touched.
- State the `EXTRACTION_VERSION` the release ships whenever that number
  changes.
- The documentation style rule above applies: factual and impersonal.
- Do not rewrite the history of released sections; add to the newest one, or
  start a new one.

## Conventions

- Run `make` targets from the repository root.
- **Python** (`python/`): Python >= 3.10, `src/` layout, standard library only
  at runtime. Lint and format with `ruff` (`make lint-python`); test with
  `pytest` (`make test-python`). CI runs both on Python 3.10 through 3.14; a
  change must work on 3.10. The conformance data is generated with Python
  3.14.
- **TypeScript** (`typescript/`): `npm test` and `npm run lint` (`make
  test-typescript`, `make lint-typescript`). CI runs Node 20, 22 and 24.
- **Rust** (`rust/`): `cargo test`, `cargo fmt --check`, `cargo clippy
  --all-targets -- -D warnings` (`make test-rust`, `make lint-rust`). CI runs
  stable Rust.
- Keep the module split and function names parallel across ports, in each
  language's own casing (`docs/ports.md`).
- `make check-vectors` and `make check-sync` must pass before a commit.
- The fixtures are real public IETF mail-archive messages. Their provenance
  requirements are in `fixtures/README.md`; do not add a fixture without
  recording what it pins and updating the counts there, and regenerate
  `conformance/` afterwards.
