# Contributing

## Development setup

The package has no runtime dependencies; the development extra adds pytest and
ruff.

With [uv](https://docs.astral.sh/uv/):

```
uv venv
uv pip install -e '.[dev]'
```

With pip:

```
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Then:

```
make test    # pytest -q
make lint    # ruff check . && ruff format --check .
```

Both targets run the tools from `./.venv` when the tree has one and from
`PATH` otherwise; force either with `make test VENV_BIN=` or
`make test VENV_BIN=.venv/bin/`. CI runs the same two commands on Python 3.10
through 3.14, so a change must work on 3.10.

## The EXTRACTION_VERSION contract

`EXTRACTION_VERSION` is an `int` in `src/email_reply_extractor/extraction.py`.
It identifies the generation of the routine that derives an extraction's text:
`extraction.py`, `cleaning.py`, `html_text.py` and the vendored `_fragments.py`
taken together. It is independent of the package version and is incremented
separately. Downstream applications store it alongside derived text and compare
it with `<` to find text that an older generation produced; the full contract
is in [docs/versioning.md](docs/versioning.md).

### When to increment

Increment it by one, by hand, in the same commit as any change to those four
modules that could alter the extracted text, the cleaned text, or an
extraction's status — a whitespace-only difference included.

Do not increment it for comments, docstrings, type annotations, or refactors
that keep every output byte identical. Do not increment it for changes outside
those four modules, and never decrement it: the value only ever increases, so
that an older consumer opening a store written by a newer routine reads that
store as current instead of offering to re-derive text it cannot reproduce.

Four generations exist: 1 (initial release of the origin application), 2 (from
its v1.2.0), 3 (from v1.11.0) and 4 (from v1.15.0, and the generation this
package ships).

### The digest, and re-recording it

`tests/test_extraction_version.py` pins the routine's real behavior: a single
SHA-256 over the composite output of every `.eml` fixture in `tests/fixtures`,
in stem order, taking three values per fixture (the stage-1-then-stage-2
composite, plus stage 1's `text` and `method`). This is stricter than the
corpus test in `tests/test_extraction.py`, which compares tolerantly; the
digest is taken over the exact strings, so a whitespace-only move fails it.

Two tests enforce the pairing, so a bump without a re-record and a re-record
without a bump are both visible in the diff of that one file:

- `EXPECTED_DIGEST` must equal the current corpus digest.
- `DIGEST_EXTRACTION_VERSION` must equal `EXTRACTION_VERSION`.

When the digest test fails, decide first whether the change in output was
intended. If it was not, revert it — the exact derived bytes, and anything a
consumer keys on them, have moved. If it was intended, increment
`EXTRACTION_VERSION`, then re-record both constants in
`tests/test_extraction_version.py` with the value printed by the command the
failure message quotes:

```
.venv/bin/python -c "import sys; sys.path.insert(0, 'tests'); import test_extraction_version as t; print(t.corpus_digest())"
```

The version constant and the two digest constants must move in the same
commit. A commit that changes one without the other leaves the repository in a
state where the stamp no longer describes the routine.

## The fixture corpus

`tests/fixtures` holds 24 hand-labeled messages: `<stem>.eml` is the raw
RFC 5322 message and `expected/<stem>.txt` is the ground truth — the composite
of both stages, `clean_for_scoring(extract_new_text(body).text).text`.
`tests/fixtures/README.md` documents the provenance requirements, how the
expected files were derived, and what each fixture pins; read it before adding
one.

To add a fixture:

1. Choose a message that pins a behavior no existing fixture pins, and record
   why in the fixtures README table. Every fixture is a real message from the
   public IETF mail archive, downloaded from its public per-message
   `/download/` endpoint; the one synthetic fixture is labeled as such and
   exists because the message that exposed the defect is not in the archive.
2. Save the raw message as `tests/fixtures/<category>-<description>-<list>-NN.eml`,
   following the existing structural-category prefixes.
3. Hand-label the expected text and save it as
   `tests/fixtures/expected/<same-stem>.txt`, applying the removal, retention
   and whitespace-normalization rules the fixtures README sets out. Hand-label
   it from the decoded body; do not paste the pipeline's own output, which
   would pin whatever the pipeline does today rather than what it should do.
4. Add the fixture's row to the table and update the category counts in
   `tests/fixtures/README.md`.
5. Re-record the corpus digest. A new fixture changes the digest without
   changing the routine, so `EXTRACTION_VERSION` is **not** incremented in
   that case; re-record `EXPECTED_DIGEST` alone and leave
   `DIGEST_EXTRACTION_VERSION` as it is.

## Documentation style

All documentation — `README.md`, everything under `docs/`, this file, and any
other published prose — is written in a simple, factual, impersonal style. No
editorializing: no opinionated flourishes, colloquialisms, first-person voice,
or subjective commentary. State facts, measurements and rationale plainly.

## Changelog

`CHANGELOG.md` records every release, newest first, and is parsed by scripts.
Its own "Format" section is the specification; keep to it exactly. Every
version bump gets a section, and bumping `__version__` without adding or
updating a section is incomplete work.
