#!/usr/bin/env python3
"""Run the host-side test suite (no hardware)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Keep receipts and config out of the operator's real home directory.
_iso = tempfile.TemporaryDirectory(prefix="nano7kit-test-")
os.environ.setdefault("XDG_CONFIG_HOME", str(Path(_iso.name) / "xdg"))
os.environ.setdefault("XDG_DATA_HOME", str(Path(_iso.name) / "data"))
os.environ.setdefault("NANO7_STATE_DIR", str(Path(_iso.name) / "state"))
os.environ.setdefault("NANO7KIT_CONFIG_DIR", str(Path(_iso.name) / "cfg"))

# Third-party files are fetched, not committed. Fetch once (cached) before the suite.
sys.path.insert(0, str(ROOT.parent))
from host import upstream  # noqa: E402

upstream.ensure()

suite = unittest.defaultTestLoader.discover(str(ROOT), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
