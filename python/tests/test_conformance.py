"""The committed conformance data matches the reference implementation.

``conformance/`` is generated from this package (``make vectors``) and is what
the TypeScript and Rust ports are tested against. These tests catch drift from
inside ``pytest``: a behaviour change that was not followed by a regeneration
fails here, on every supported Python version, before CI's byte-level
``make check-vectors`` runs. They also replay every recorded vector through the
Python implementation, the same way a port's harness does.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest
from conformance_support import (
    CORPUS_PATH,
    DIGEST_PATH,
    FORMAT,
    VECTORS_DIR,
    corpus_entry,
    digest_of_entries,
    encode,
    error_name,
    load,
)
from corpus import ALL_STEMS

_CORPUS = load(CORPUS_PATH)
_VECTOR_FILES = sorted(VECTORS_DIR.rglob("*.json"))


def test_corpus_lists_every_fixture_in_stem_order():
    assert _CORPUS["format"] == FORMAT
    assert [entry["stem"] for entry in _CORPUS["entries"]] == ALL_STEMS


@pytest.mark.parametrize("index", range(len(ALL_STEMS)), ids=ALL_STEMS)
def test_corpus_entry_matches_live_output(index):
    stem = ALL_STEMS[index]
    assert _CORPUS["entries"][index] == corpus_entry(stem), (
        f"conformance/corpus.json is out of date for {stem}; run `make vectors`."
    )


def test_digest_is_computable_from_the_corpus_json_alone():
    """The algorithm a port implements reproduces the recorded digest."""
    assert digest_of_entries(_CORPUS["entries"]) == load(DIGEST_PATH)["digest"]


def test_every_public_function_has_vectors():
    import email_reply_extractor

    public = {
        name
        for name in email_reply_extractor.__all__
        if callable(getattr(email_reply_extractor, name))
        and not isinstance(getattr(email_reply_extractor, name), type)
    }
    recorded = {p.stem for p in VECTORS_DIR.glob("*.json")}
    assert recorded == public


def _function(spec: dict) -> object:
    module = spec["module"]
    if module == "fragments":
        module = "_fragments"
    return getattr(importlib.import_module(f"email_reply_extractor.{module}"), spec["function"])


@pytest.mark.parametrize(
    "path", _VECTOR_FILES, ids=[p.relative_to(VECTORS_DIR).as_posix() for p in _VECTOR_FILES]
)
def test_vectors_replay(path: pathlib.Path):
    spec = load(path)
    assert spec["format"] == FORMAT
    assert spec["vectors"], "a vector file with no vectors pins nothing"
    func = _function(spec)
    for vector in spec["vectors"]:
        # Every recorded argument is named; none of the functions has a
        # positional-only parameter, so passing all of them by keyword is exact.
        if "error" in vector:
            with pytest.raises(Exception) as excinfo:
                func(**vector["args"])
            assert error_name(excinfo.value) == vector["error"]
        else:
            assert encode(func(**vector["args"])) == vector["result"], vector["args"]
