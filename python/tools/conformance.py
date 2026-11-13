"""Generate or check the language-neutral conformance data under ``conformance/``.

Usage (from anywhere; ``make vectors`` and ``make check-vectors`` wrap it)::

    python python/tools/conformance.py            # regenerate conformance/
    python python/tools/conformance.py --check    # regenerate to a temporary
                                                  # directory and report drift
    python python/tools/conformance.py --rerecord-digest

What it writes (formats in ``conformance/README.md``):

- ``corpus.json`` — every fixture's decoded bodies and the reference outputs.
- ``digest.json`` — the corpus digest and the generation it was recorded
  against. The digest is re-recorded only when ``EXTRACTION_VERSION`` was
  incremented, when the fixture set changed, or with ``--rerecord-digest``;
  otherwise a moved digest is an error, because it means the derived text
  changed without a generation bump.
- ``vectors/<function>.json`` and ``vectors/internal/<module>.<function>.json``
  — call-level vectors recorded while the Python unit tests run. Every library
  function a test module imports is wrapped; each call's arguments (by name)
  and result (or exception type) are logged, de-duplicated and sorted. The
  calls the pipeline makes to the vendored fragment scanner are recorded too.

The recording run excludes ``test_conformance.py`` and
``test_extraction_version.py``: those check this data rather than exercising
behaviour, and they would fail mid-way through an intended regeneration.
"""

from __future__ import annotations

import argparse
import functools
import inspect
import json
import pathlib
import sys
import tempfile
from typing import Any, Callable

PYTHON_DIR = pathlib.Path(__file__).resolve().parents[1]
TESTS_DIR = PYTHON_DIR / "tests"
sys.path.insert(0, str(PYTHON_DIR / "src"))
sys.path.insert(0, str(TESTS_DIR))

import email_reply_extractor  # noqa: E402
import pytest  # noqa: E402
from conformance_support import (  # noqa: E402
    CONFORMANCE_DIR,
    FORMAT,
    corpus_entries,
    digest_of_entries,
    dumps,
    encode,
    error_name,
    load,
)
from email_reply_extractor.extraction import EXTRACTION_VERSION  # noqa: E402

#: Test modules left out of the recording run (see the module docstring).
_NOT_RECORDED = ("test_conformance.py", "test_extraction_version.py")

#: Library-internal call sites also recorded, as ``(module, name)``: the bound
#: name inside ``module`` is wrapped, so every call the pipeline makes through
#: it is logged. The vendored fragment scanner has no unit tests of its own;
#: recording its calls from the corpus and unit tests gives a port a way to
#: localize a scanner mismatch.
_INTERNAL_CALL_SITES = (("email_reply_extractor.extraction", "read_fragments"),)

#: Files under ``conformance/`` that are maintained by hand, not generated.
_HAND_WRITTEN = frozenset({"README.md"})


# --- recording ----------------------------------------------------------------


def _module_name(func: Callable[..., Any]) -> str:
    """``email_reply_extractor._fragments`` -> ``fragments``."""
    return func.__module__.rsplit(".", 1)[-1].lstrip("_")


def _is_library_function(value: Any) -> bool:
    return inspect.isfunction(value) and value.__module__.startswith("email_reply_extractor.")


class _Recorder:
    """A pytest plugin that wraps library functions in every collected test module."""

    def __init__(self) -> None:
        self.public = {
            getattr(email_reply_extractor, name)
            for name in email_reply_extractor.__all__
            if _is_library_function(getattr(email_reply_extractor, name))
        }
        #: function -> {args-key: vector}
        self.calls: dict[Callable[..., Any], dict[str, dict[str, Any]]] = {}
        self._wrappers: dict[Callable[..., Any], Callable[..., Any]] = {}

    def _wrap(self, func: Callable[..., Any]) -> Callable[..., Any]:
        if func in self._wrappers:
            return self._wrappers[func]
        signature = inspect.signature(func)
        calls = self.calls.setdefault(func, {})

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind(*args, **kwargs)
            # Encode before the call: a callee that mutated a list argument
            # must not change what was recorded as its input.
            recorded_args = {name: encode(value) for name, value in bound.arguments.items()}
            key = json.dumps(recorded_args, ensure_ascii=True)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                vector = {"args": recorded_args, "error": error_name(exc)}
                _store(calls, key, vector, func)
                raise
            _store(calls, key, {"args": recorded_args, "result": encode(result)}, func)
            return result

        self._wrappers[func] = wrapper
        return wrapper

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        modules = {getattr(item, "module", None) for item in session.items}
        for module in modules:
            if module is None:
                continue
            for name, value in list(vars(module).items()):
                if _is_library_function(value):
                    setattr(module, name, self._wrap(value))
        for module_name, name in _INTERNAL_CALL_SITES:
            module = sys.modules[module_name]
            setattr(module, name, self._wrap(getattr(module, name)))


def _store(calls: dict[str, dict[str, Any]], key: str, vector: dict[str, Any], func: Any) -> None:
    previous = calls.get(key)
    if previous is not None and previous != vector:
        raise AssertionError(f"{func.__qualname__} is not deterministic for {key[:200]}")
    calls[key] = vector


def _parameters(func: Callable[..., Any]) -> list[dict[str, Any]]:
    out = []
    for param in inspect.signature(func).parameters.values():
        spec: dict[str, Any] = {"name": param.name, "kind": param.kind.name.lower()}
        if param.default is not inspect.Parameter.empty:
            spec["default"] = encode(param.default)
        out.append(spec)
    return out


def _record_vectors() -> tuple[_Recorder, int]:
    recorder = _Recorder()
    args = ["-q", "-p", "no:cacheprovider", "--rootdir", str(PYTHON_DIR)]
    args += [f"--ignore={TESTS_DIR / name}" for name in _NOT_RECORDED]
    args.append(str(TESTS_DIR))
    status = pytest.main(args, plugins=[recorder])
    return recorder, int(status)


def _vector_files(recorder: _Recorder) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for func, calls in recorder.calls.items():
        public = func in recorder.public
        module = _module_name(func)
        path = f"vectors/{func.__name__}.json"
        if not public:
            path = f"vectors/internal/{module}.{func.__name__}.json"
        returns = inspect.signature(func).return_annotation
        files[path] = {
            "format": FORMAT,
            "function": func.__name__,
            "module": module,
            "public": public,
            "parameters": _parameters(func),
            "returns": returns if isinstance(returns, str) else getattr(returns, "__name__", None),
            "vectors": [calls[key] for key in sorted(calls)],
        }
    return dict(sorted(files.items()))


# --- digest policy ------------------------------------------------------------


def _digest_file(
    entries: list[dict[str, Any]], rerecord: bool
) -> tuple[dict[str, Any], str | None]:
    """Return the ``digest.json`` content to write, and an error message or None."""
    digest = digest_of_entries(entries)
    fresh = {"format": FORMAT, "extraction_version": EXTRACTION_VERSION, "digest": digest}
    path = CONFORMANCE_DIR / "digest.json"
    if not path.exists():
        return fresh, None
    recorded = load(path)
    recorded_version = recorded["extraction_version"]
    if EXTRACTION_VERSION < recorded_version:
        return recorded, (
            f"EXTRACTION_VERSION ({EXTRACTION_VERSION}) is lower than the generation "
            f"digest.json was recorded against ({recorded_version}); it only ever increases."
        )
    if recorded["digest"] == digest or EXTRACTION_VERSION > recorded_version or rerecord:
        return fresh, None
    old_corpus = CONFORMANCE_DIR / "corpus.json"
    if old_corpus.exists():
        old_stems = [entry["stem"] for entry in load(old_corpus)["entries"]]
        if old_stems != [entry["stem"] for entry in entries]:
            # A fixture was added or removed: the digest moves, the generation does not.
            return fresh, None
    return recorded, (
        "The corpus digest moved but EXTRACTION_VERSION did not: the derived text changed.\n"
        "If the change is intended, increment EXTRACTION_VERSION in "
        "python/src/email_reply_extractor/extraction.py and run `make vectors` again.\n"
        "If a fixture's .eml was edited (the routine itself unchanged), re-record with\n"
        "  python python/tools/conformance.py --rerecord-digest\n"
        "Otherwise revert the change: the exact derived bytes have moved.\n"
        f"  recorded: {recorded['digest']} (generation {recorded_version})\n"
        f"  current:  {digest}"
    )


# --- generation ---------------------------------------------------------------


def build(rerecord: bool) -> tuple[dict[str, str], list[str]]:
    """Return ``{relative path: file text}`` for every generated file, and errors."""
    errors: list[str] = []
    entries = corpus_entries()
    files: dict[str, Any] = {"corpus.json": {"format": FORMAT, "entries": entries}}
    digest, error = _digest_file(entries, rerecord)
    files["digest.json"] = digest
    if error:
        errors.append(error)

    recorder, status = _record_vectors()
    if status != 0:
        errors.append(f"the Python test suite failed during recording (pytest exit {status})")
    files.update(_vector_files(recorder))
    return {path: dumps(content) for path, content in files.items()}, errors


def _existing(root: pathlib.Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*.json"))
        if p.relative_to(root).as_posix() not in _HAND_WRITTEN
    }


def write(files: dict[str, str], root: pathlib.Path) -> None:
    for stale in set(_existing(root)) - set(files):
        (root / stale).unlink()
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def check(files: dict[str, str]) -> list[str]:
    """Compare freshly generated ``files`` with the tree's ``conformance/``."""
    with tempfile.TemporaryDirectory() as tmp:
        write(files, pathlib.Path(tmp))
        fresh = _existing(pathlib.Path(tmp))
    on_disk = _existing(CONFORMANCE_DIR)
    problems = []
    for rel in sorted(set(fresh) | set(on_disk)):
        if rel not in on_disk:
            problems.append(f"missing from conformance/: {rel}")
        elif rel not in fresh:
            problems.append(f"no longer generated: conformance/{rel}")
        elif fresh[rel] != on_disk[rel]:
            problems.append(f"out of date: conformance/{rel}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="report drift; write nothing")
    parser.add_argument(
        "--rerecord-digest",
        action="store_true",
        help="accept a moved corpus digest without an EXTRACTION_VERSION increment",
    )
    args = parser.parse_args(argv)

    files, errors = build(args.rerecord_digest)
    if args.check:
        errors += check(files)
        if errors:
            errors.append("Run `make vectors` and commit the result.")
    else:
        write(files, CONFORMANCE_DIR)
        counts = {
            rel: len(json.loads(text)["vectors"])
            for rel, text in files.items()
            if rel.startswith("vectors/")
        }
        print(f"wrote {len(files)} files under {CONFORMANCE_DIR}")
        for rel, count in counts.items():
            print(f"  {rel}: {count} vectors")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
