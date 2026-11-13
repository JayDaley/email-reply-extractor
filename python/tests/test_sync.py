"""The ports agree on the package version, EXTRACTION_VERSION and the license.

Runs ``python/tools/check_sync.py`` (also ``make check-sync``) from inside
``pytest``, so a port left on an old version number fails the Python suite
too. A port whose directory does not exist yet is skipped by the tool.
"""

from __future__ import annotations

import importlib.util

from corpus import REPO_ROOT


def test_ports_are_in_sync():
    path = REPO_ROOT / "python" / "tools" / "check_sync.py"
    spec = importlib.util.spec_from_file_location("check_sync", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check() == []
