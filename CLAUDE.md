# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

`email-reply-extractor` — a Python library that recovers the author's newly
written text from an email in two stages. Pure standard library, no runtime
dependencies, Python >= 3.10.

## Layout

- `src/email_reply_extractor/` — package source (src layout)
  - `__init__.py` — the public API and `__version__`
  - `extraction.py` — stage 1 (`extract_new_text`) and `EXTRACTION_VERSION`
  - `cleaning.py` — stage 2 (`clean_for_scoring`)
  - `html_text.py` — `html_to_text` / `split_html_parts`, the HTML oracle
  - `_fragments.py` — the vendored email-reply-parser fragment scanner
- `tests/` — pytest suite, including `fixtures/` (hand-labeled corpus of 24
  real messages) and `test_extraction_version.py` (the digest contract)
- `docs/` — `extraction.md` (the rules of both stages), `versioning.md` (the
  `EXTRACTION_VERSION` contract), `generation-5.md` (planned output changes)
- `pyproject.toml` — project metadata, dependencies, tooling config
- `Makefile` — `make test`, `make lint`
- `CHANGELOG.md` — one section per release (see Changelog below)
- `CONTRIBUTING.md` — development setup and the contracts contributors follow

The module docstrings in the four source modules are the authoritative design
documentation; `docs/` restructures them for readers. Keep the two in step: a
rule change that touches a docstring touches `docs/extraction.md` as well.

## Documentation style — hard rule

All documentation (`README.md`, everything under `docs/`, `CONTRIBUTING.md`,
and any other published prose) must be written in a simple, factual,
impersonal style. No editorializing: no opinionated flourishes,
colloquialisms, first-person voice, or subjective commentary — state facts,
measurements and rationale plainly.

## Extraction version

`EXTRACTION_VERSION` (an `int` in `extraction.py`, currently 5) identifies the
generation of the routine that derives an extraction's text: `extraction.py`,
`cleaning.py`, `html_text.py` and `_fragments.py` taken together. It is
independent of the package version and is incremented separately.

- Increment it by one, by hand, in the same commit as any change to those four
  modules that could alter the extracted text, the cleaned text, or an
  extraction's status — a whitespace-only difference included. Do not
  increment it for comments, docstrings, type annotations or refactors that
  keep every output byte identical.
- It only ever increases. Consumers compare a stored stamp with the running
  value using `<`, so an older consumer opening a store written by a newer
  routine reads that store as current instead of offering to re-derive text it
  cannot reproduce.
- `tests/test_extraction_version.py` pins the routine's output over the fixture
  corpus as a single SHA-256. An increment requires re-recording
  `EXPECTED_DIGEST` and `DIGEST_EXTRACTION_VERSION` in that file in the same
  commit; the test fails otherwise, and it also fails when the output moves
  without an increment. The failure message quotes the command that prints the
  current digest.
- Adding a fixture moves the digest without changing the routine: re-record
  `EXPECTED_DIGEST` alone and leave both version numbers where they are.
- Five generations exist: **1** (initial release of the origin application),
  **2** (from its v1.2.0), **3** (from v1.11.0), **4** (from v1.15.0, the
  generation this package's v1.0.0 shipped) and **5** (this package's v1.1.0:
  fragment edges kept, Outlook-boundary fix uncapped).

## The vendored scanner

`_fragments.py` is trimmed from `email-reply-parser` 0.5.12. Two upstream
defects (a `count=8` cap on the Outlook-boundary fix; per-fragment edge
stripping) were preserved through generation 4 and fixed in generation 5 —
see `docs/generation-5.md`. One upstream behavior remains and is deliberate:
the wrapped-attribution collapse, including its template-substitution failure
mode. `extraction.py`'s quote-header pre-truncation compensates for the
collapse's line-gluing and must stay until the collapse itself is removed;
that removal is deferred, with the measured evidence, in
`docs/generation-5.md`. Any change to the scanner that can move output bytes
is a generation bump with a digest re-record.

## Versioning

The package uses [semantic versioning](https://semver.org/); the current
version is **1.1.1**. The single source of truth is
`email_reply_extractor.__version__` (in `__init__.py`); `pyproject.toml` reads
it dynamically, so the two never drift.

Bump policy — the component to bump is decided by whether the change affects
the text consumers derive, not by how large or how visible it is:

- **major** — a breaking change to the public API: a removed or renamed export,
  a changed signature, a changed return shape.
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
Its own "Format" section is the specification; keep to it exactly.

- Every version bump gets a section. Bumping `__version__` without adding or
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

- Python >= 3.10, `src/` layout, standard library only at runtime.
- Lint and format with `ruff` (`make lint`); test with `pytest` (`make test`).
- CI runs both on Python 3.10 through 3.14; a change must work on 3.10.
- The fixtures are real public IETF mail-archive messages. Their provenance
  requirements are in `tests/fixtures/README.md`; do not add a fixture without
  recording what it pins and updating the counts there.
