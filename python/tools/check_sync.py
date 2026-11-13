"""Check that the ports agree on the values they must share.

Usage: ``python python/tools/check_sync.py`` (``make check-sync`` wraps it).

Checks, skipping a port whose directory does not exist yet:

- the package version: ``python/src/email_reply_extractor/__init__.py``
  ``__version__``, ``typescript/package.json`` ``version`` and
  ``rust/Cargo.toml`` ``[package] version`` are equal;
- ``EXTRACTION_VERSION``: the constant in ``python/.../extraction.py``,
  ``typescript/src/extraction.ts`` and ``rust/src/extraction.rs`` equals
  ``extraction_version`` in ``conformance/digest.json``;
- the license: ``python/LICENSE`` (and ``typescript/LICENSE`` /
  ``rust/LICENSE`` when those ports exist) is byte-identical to the root
  ``LICENSE``. Package builds cannot reach outside their port's directory, so
  each port carries a copy.

Standard library only, and no import of the package, so it runs on any
supported Python before anything is installed.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_PY_INIT = "python/src/email_reply_extractor/__init__.py"
_PY_EXTRACTION = "python/src/email_reply_extractor/extraction.py"
_TS_EXTRACTION = "typescript/src/extraction.ts"
_RS_EXTRACTION = "rust/src/extraction.rs"

_PY_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
_PY_EV_RE = re.compile(r"^EXTRACTION_VERSION\s*(?::\s*int\s*)?=\s*(\d+)\s*$", re.MULTILINE)
_TS_EV_RE = re.compile(
    r"^export const EXTRACTION_VERSION(?:\s*:\s*number)?\s*=\s*(\d+);?\s*$", re.MULTILINE
)
_RS_EV_RE = re.compile(r"^pub const EXTRACTION_VERSION\s*:\s*u32\s*=\s*(\d+)\s*;\s*$", re.MULTILINE)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _one(pattern: re.Pattern[str], rel: str, what: str, errors: list[str]) -> str | None:
    path = REPO_ROOT / rel
    if not path.exists():
        errors.append(f"{rel} is missing ({what} expected there)")
        return None
    found = pattern.findall(_read(rel))
    if len(found) != 1:
        errors.append(f"{rel}: expected exactly one {what}, found {len(found)}")
        return None
    return found[0]


def _cargo_version(errors: list[str]) -> str | None:
    """``version`` from the ``[package]`` table of ``rust/Cargo.toml``."""
    rel = "rust/Cargo.toml"
    if not (REPO_ROOT / rel).exists():
        errors.append(f"{rel} is missing")
        return None
    table = None
    for line in _read(rel).splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            table = stripped
            continue
        m = re.match(r'version\s*=\s*"([^"]+)"', stripped)
        if table == "[package]" and m:
            return m.group(1)
    errors.append(f"{rel}: no version in [package]")
    return None


def check() -> list[str]:
    errors: list[str] = []
    versions: dict[str, str | None] = {}
    generations: dict[str, str | None] = {}
    ports = ["python"] + [p for p in ("typescript", "rust") if (REPO_ROOT / p).is_dir()]

    versions[_PY_INIT] = _one(_PY_VERSION_RE, _PY_INIT, "__version__", errors)
    generations[_PY_EXTRACTION] = _one(_PY_EV_RE, _PY_EXTRACTION, "EXTRACTION_VERSION", errors)

    if "typescript" in ports:
        rel = "typescript/package.json"
        if (REPO_ROOT / rel).exists():
            versions[rel] = json.loads(_read(rel)).get("version")
        else:
            errors.append(f"{rel} is missing")
        generations[_TS_EXTRACTION] = _one(
            _TS_EV_RE, _TS_EXTRACTION, "`export const EXTRACTION_VERSION = <n>`", errors
        )

    if "rust" in ports:
        versions["rust/Cargo.toml"] = _cargo_version(errors)
        generations[_RS_EXTRACTION] = _one(
            _RS_EV_RE, _RS_EXTRACTION, "`pub const EXTRACTION_VERSION: u32 = <n>;`", errors
        )

    digest_path = REPO_ROOT / "conformance" / "digest.json"
    if digest_path.exists():
        recorded = json.loads(digest_path.read_text(encoding="utf-8"))["extraction_version"]
        generations["conformance/digest.json"] = str(recorded)
    else:
        errors.append("conformance/digest.json is missing; run `make vectors`")

    if len({v for v in versions.values() if v is not None}) > 1:
        listing = ", ".join(f"{k}={v}" for k, v in versions.items())
        errors.append(f"package versions differ: {listing}")
    if len({v for v in generations.values() if v is not None}) > 1:
        listing = ", ".join(f"{k}={v}" for k, v in generations.items())
        errors.append(f"EXTRACTION_VERSION differs: {listing}")

    license_bytes = (REPO_ROOT / "LICENSE").read_bytes()
    for port in ports:
        copy = REPO_ROOT / port / "LICENSE"
        if not copy.exists():
            errors.append(f"{port}/LICENSE is missing (copy the root LICENSE)")
        elif copy.read_bytes() != license_bytes:
            errors.append(f"{port}/LICENSE differs from the root LICENSE")
    return errors


def main() -> int:
    errors = check()
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if not errors:
        print("ports in sync")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
