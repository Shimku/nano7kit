# SPDX-License-Identifier: MIT
"""NanoApps-derived apps are built on demand, not shipped. Tests that need
one build it the same way `./start apps add` does, or skip without a compiler."""

from __future__ import annotations

import contextlib
import io
import unittest

from host.compat import which


def need_built(tc: unittest.TestCase, *names: str) -> None:
    from host.app_catalog import by_name, reset_catalog_cache
    from host.apps import ensure_built

    reset_catalog_cache()
    rows = by_name()
    missing = [n for n in names if not rows[n].get("present")]
    if not missing:
        return
    if not which("arm-none-eabi-gcc"):
        tc.skipTest(f"needs {', '.join(missing)} built (arm-none-eabi-gcc missing)")
    with contextlib.redirect_stdout(io.StringIO()):
        for n in missing:
            row = ensure_built(n)
            if not row or not row.get("present"):
                tc.fail(f"could not build {n}")
