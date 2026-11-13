# Contributing

The library is maintained in three ports in this repository: Python
(`python/`, the reference implementation), TypeScript (`typescript/`) and Rust
(`rust/`). They share one version, one `EXTRACTION_VERSION`, one changelog and
one conformance suite (`conformance/`). The contract between them is
[docs/ports.md](docs/ports.md). Run every `make` target from the repository
root.

## Development setup

### Python

The package has no runtime dependencies; the development extra adds pytest and
ruff. The virtual environment lives at the repository root (`.venv`), and the
package is installed from `python/`.

With [uv](https://docs.astral.sh/uv/):

```
uv venv --python 3.14
uv pip install -e 'python[dev]'
```

With pip:

```
python3 -m venv .venv
.venv/bin/pip install -e 'python[dev]'
```

Then:

```
make test-python     # cd python && pytest -q
make lint-python     # cd python && ruff check . && ruff format --check .
```

The Python targets run the tools from `./.venv` when the tree has one and from
`PATH` otherwise; force either with `make test VENV_BIN=` or
`make test VENV_BIN=$(pwd)/.venv/bin/`. CI runs both on Python 3.10 through
3.14, so a change must work on 3.10. The conformance data is generated with
Python 3.14, so use 3.14 for `make vectors`.

### TypeScript

Requires Node.js 20 or later.

```
cd typescript && npm ci
make test-typescript   # cd typescript && npm test
make lint-typescript   # cd typescript && npm run lint
```

### Rust

Requires stable Rust with `rustfmt` and `clippy`.

```
make test-rust   # cd rust && cargo test
make lint-rust   # cd rust && cargo fmt --check && cargo clippy --all-targets -- -D warnings
```

### Whole repository

```
make test            # every port whose directory exists
make lint            # likewise
make vectors         # regenerate conformance/ from the Python reference
make check-vectors   # fail if conformance/ differs from a fresh regeneration
make check-sync      # versions, EXTRACTION_VERSION and LICENSE copies agree
```

CI runs `make check-vectors check-sync` in its `conformance` job.

## Changing extraction behaviour

Python is the reference implementation. A change that can alter any output of
the library follows this procedure, and is incomplete until every step is done:

1. Change the Python implementation in `python/src/` and add or update Python
   unit tests that pin the new behaviour. The unit tests are the source of the
   call-level conformance vectors, so behaviour that no Python test exercises
   is not checked in the other ports.
2. If the derived text can change, increment `EXTRACTION_VERSION` (see below)
   in all three ports.
3. Run `make vectors`. It regenerates `conformance/corpus.json`, the vectors
   and, after an increment, `conformance/digest.json`. Review the diff of
   `conformance/`: every changed output should be one the change intended.
4. Change the TypeScript and Rust ports until `make test` passes for all
   three, and `make lint`, `make check-vectors` and `make check-sync` pass.
5. Bump the package version in all three ports and add a `CHANGELOG.md`
   section (see Changelog below). Commit the three ports, `conformance/` and
   the changelog together.

A change that touches only one port and keeps every output identical (a
refactor, a packaging change, a port-specific bug fix that brings a port into
line with the vectors) follows the same versioning rules but needs no
regeneration.

## The EXTRACTION_VERSION contract

`EXTRACTION_VERSION` is an integer declared in
`python/src/email_reply_extractor/extraction.py`, `typescript/src/extraction.ts`
and `rust/src/extraction.rs`. It identifies the generation of the routine that
derives an extraction's text: `extraction.py`, `cleaning.py`, `html_text.py`
and the vendored `_fragments.py` taken together, and their counterparts in the
ports. It is independent of the package version and is incremented
separately. Downstream applications store it alongside derived text and
compare it with `<` to find text that an older generation produced; the full
contract is in [docs/versioning.md](docs/versioning.md).

### When to increment

Increment it by one, by hand, in every port, in the same commit as any change
to those modules that could alter the extracted text, the cleaned text, or an
extraction's status — a whitespace-only difference included.

Do not increment it for comments, docstrings, type annotations, or refactors
that keep every output byte identical. Do not increment it for changes outside
those modules, and never decrement it: the value only ever increases, so that
an older consumer opening a store written by a newer routine reads that store
as current instead of offering to re-derive text it cannot reproduce.

Six generations exist: 1 (initial release of the origin application), 2 (from
its v1.2.0), 3 (from v1.11.0), 4 (from v1.15.0, shipped by this package's
v1.0.0), 5 (this package's v1.1.0) and 6 (this package's v1.2.0).

### The digest, and re-recording it

`conformance/digest.json` pins the routine's real behavior: a single SHA-256
over the composite output of every `.eml` fixture in `fixtures/`, in stem
order, taking four values per fixture (the stem, the stage-1-then-stage-2
composite, and stage 1's `text` and `method`). It is the only place the digest
is recorded; every port's test suite computes it from its own output and
compares. The algorithm is specified in
[conformance/README.md](conformance/README.md). The digest is stricter than
the corpus test in `python/tests/test_extraction.py`, which compares
tolerantly; it is taken over the exact strings, so a whitespace-only move
fails it.

`python/tests/test_extraction_version.py` enforces the pairing: the live
digest must equal the recorded `digest`, and the recorded
`extraction_version` must equal `EXTRACTION_VERSION`.

When the digest test fails, decide first whether the change in output was
intended. If it was not, revert it — the exact derived bytes, and anything a
consumer keys on them, have moved. If it was intended, increment
`EXTRACTION_VERSION` in every port and run `make vectors`, which re-records
`conformance/digest.json`. `make vectors` refuses to re-record a moved digest
when the constant was not incremented and the fixture set did not change.

The constants and `conformance/digest.json` must move in the same commit. A
commit that changes one without the other leaves the repository in a state
where the stamp no longer describes the routine.

## The fixture corpus

`fixtures/` holds 26 hand-labeled messages: `<stem>.eml` is the raw RFC 5322
message and `expected/<stem>.txt` is the ground truth — the composite of both
stages, `clean_for_scoring(extract_new_text(body).text).text`.
`fixtures/README.md` documents the provenance requirements, how the expected
files were derived, and what each fixture pins; read it before adding one.
Only the Python suite parses the `.eml` files; the decoded bodies reach the
other ports through `conformance/corpus.json`.

To add a fixture:

1. Choose a message that pins a behavior no existing fixture pins, and record
   why in the fixtures README table. Every fixture is a real message from the
   public IETF mail archive, downloaded from its public per-message
   `/download/` endpoint; the one synthetic fixture is labeled as such and
   exists because the message that exposed the defect is not in the archive.
2. Save the raw message as `fixtures/<category>-<description>-<list>-NN.eml`,
   following the existing structural-category prefixes.
3. Hand-label the expected text and save it as
   `fixtures/expected/<same-stem>.txt`, applying the removal, retention and
   whitespace-normalization rules the fixtures README sets out. Hand-label it
   from the decoded body; do not paste the pipeline's own output, which would
   pin whatever the pipeline does today rather than what it should do.
4. Add the fixture's row to the table and update the category counts in
   `fixtures/README.md`.
5. Run `make vectors`. A new fixture changes the digest without changing the
   routine, so `EXTRACTION_VERSION` is **not** incremented in that case;
   `make vectors` re-records the digest and leaves the generation as it is.
6. Run `make test` for all three ports: the new corpus entry must pass in
   each.

## Releasing

Each `v*` tag publishes all three packages from the same commit:

- `.github/workflows/publish.yml` builds `python/` and publishes it to PyPI
  through PyPI Trusted Publishing (OIDC).
- `.github/workflows/publish-npm.yml` publishes `typescript/` to npm with
  provenance through npm trusted publishing (OIDC).
- `.github/workflows/publish-crates.yml` publishes `rust/` to crates.io with a
  token from the `CARGO_REGISTRY_TOKEN` repository secret.

No secret value is stored in the repository. The maintainer configures, once:

- On PyPI: a trusted publisher for this repository, workflow `publish.yml`,
  environment `pypi`.
- On npmjs.com: a trusted publisher for the `email-reply-extractor` package,
  pointing at this repository and workflow `publish-npm.yml`, environment
  `npm`. Trusted publishing requires the package to exist on npm; the first
  release is published by hand with `npm publish --access public` from
  `typescript/`. Provenance is generated only inside a supported CI system,
  so a release published by hand carries none.
- On crates.io: an API token with the `publish-update` (and, for the first
  release, `publish-new`) scope, stored as the repository secret
  `CARGO_REGISTRY_TOKEN` in a GitHub environment named `crates-io`.
- In the repository settings: the `pypi`, `npm` and `crates-io` environments.

Before tagging, `make check-sync` must pass, so the three manifests carry the
tagged version.

## Documentation style

All documentation — `README.md`, every port's `README.md`, everything under
`docs/`, `conformance/README.md`, `fixtures/README.md`, this file, and any
other published prose — is written in a simple, factual, impersonal style. No
editorializing: no opinionated flourishes, colloquialisms, first-person voice,
or subjective commentary. State facts, measurements and rationale plainly.

## Changelog

`CHANGELOG.md` records every release of all three ports, newest first, and is
parsed by scripts. Its own "Format" section is the specification; keep to it
exactly. Every version bump gets a section, and bumping the version without
adding or updating a section is incomplete work.
