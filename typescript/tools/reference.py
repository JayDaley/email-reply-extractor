"""Evaluate calls with the Python reference implementation, for tools/differential.mjs.

Reads a JSON array of ``{"fn": "<module>.<function>", "args": {...}}`` from
standard input and writes a JSON array of ``{"result": ...}`` or
``{"error": "<exception class>"}``, encoded as in ``conformance/README.md``.
Run with the Python that generates ``conformance/`` and with the reference
package importable (``.venv`` at the repository root).
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import sys


def encode(value):
    if dataclasses.is_dataclass(value):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def main() -> int:
    calls = json.load(sys.stdin)
    out = []
    for call in calls:
        module, name = call["fn"].rsplit(".", 1)
        fn = getattr(importlib.import_module(f"email_reply_extractor.{module}"), name)
        try:
            out.append({"result": encode(fn(**call["args"]))})
        except Exception as exc:  # noqa: BLE001 - the error is the recorded result
            out.append({"error": type(exc).__name__})
    json.dump(out, sys.stdout, ensure_ascii=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
