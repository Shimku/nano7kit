#!/usr/bin/env python3
"""apps add PvZ: memory verdict only (Plants vs. Zombies does not fit the nano heap)."""
import os
import io
import plistlib
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import pvz_fit
from host.compat import Refused

REFERENCE_IPA = Path(os.environ.get("NANO7KIT_PVZ_IPA", "/nonexistent/pvz.ipa"))


def _fake_ipa(path: Path, bundle="com.popcap.PvZ", reanims=None):
    reanims = reanims if reanims is not None else {"zombie": 1000, "selectorscreen": 2000}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Payload/PvZ.app/Info.plist", plistlib.dumps({"CFBundleIdentifier": bundle}))
        for name, size in reanims.items():
            body = zlib.compress(b"\0" * size)
            zf.writestr(
                f"Payload/PvZ.app/compiled/reanim/{name}.reanim.compiled",
                struct.pack("<II", 0xDEADFED4, size) + body,
            )


class PvzFitTests(unittest.TestCase):
    def test_pinned_model_no_fit_on_probe_heap(self):
        m = pvz_fit.model(None)
        self.assertGreater(m["guest_block"], 4 << 20)
        self.assertEqual(pvz_fit.verdict(m, int(6.4 * (1 << 20)), 4 << 20), "NO_FIT")
        lines = pvz_fit.plan_lines(None)
        self.assertEqual(lines[-1], "PLAN_REFUSED")
        self.assertTrue(any("NO_FIT" in l for l in lines))

    def test_measures_reanims_from_ipa(self):
        with tempfile.TemporaryDirectory() as td:
            ipa = Path(td) / "pvz.ipa"
            _fake_ipa(ipa)
            m = pvz_fit.model(ipa)
            self.assertEqual(m["source"], "measured from your ipa")
            self.assertEqual(m["reanim_first_level"], 3000)
            self.assertEqual(m["reanim_all"], 3000)

    def test_wrong_bundle_refused(self):
        with tempfile.TemporaryDirectory() as td:
            ipa = Path(td) / "fn.ipa"
            _fake_ipa(ipa, bundle="com.Halfbrick.Fruit")
            with self.assertRaises(Refused):
                pvz_fit.model(ipa)

    def test_huge_heap_still_refused_not_written(self):
        lines = pvz_fit.plan_lines(None, heap_free=64 << 20, largest=32 << 20)
        self.assertEqual(lines[-1], "PLAN_REFUSED")
        self.assertTrue(any("not written" in l for l in lines))

    def test_refuse_add_prints_numbers(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(Refused):
            pvz_fit.refuse_add(None)
        self.assertIn("guest", buf.getvalue())
        self.assertNotIn("\u2014", buf.getvalue())

    def test_cli_plan_and_add(self):
        from host import cli

        for argv, want in (
            (["apps", "add", "PvZ", "--plan", "--heap-free", "6.4M", "--largest", "4.0M"], 1),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(argv) if hasattr(cli, "main") else None
            if rc is None:
                self.skipTest("cli.main not exposed")
            self.assertEqual(rc, want)
            self.assertIn("PLAN_REFUSED", buf.getvalue())

    @unittest.skipUnless(REFERENCE_IPA.is_file(), "set NANO7KIT_PVZ_IPA to run")
    def test_reference_ipa(self):
        m = pvz_fit.model(REFERENCE_IPA)
        self.assertEqual(m["reanim_top"][0][0], "zombie_boss")
        self.assertGreater(m["reanim_first_level"], 3 << 20)


if __name__ == "__main__":
    unittest.main()


class PvzBestCaseTests(unittest.TestCase):
    def test_best_case_after_lean_is_still_not_a_build(self):
        from host import pvz_fit

        m = pvz_fit.model(None)
        self.assertEqual(m["best_total"], m["total"] - pvz_fit.PINNED_REANIM["selectorscreen"])
        self.assertEqual(m["best_block"], pvz_fit.DATA_SEGMENT)
        self.assertEqual(pvz_fit.LEAN_SAVED, 327_680)
        # lean residents: the one-block model still refuses
        self.assertEqual(pvz_fit.verdict(m, pvz_fit.LEAN_FREE, pvz_fit.LEAN_LARGEST_BEST), "NO_FIT")
        self.assertEqual(pvz_fit.verdict(m, int(7.4 * (1 << 20)), 5 << 20), "NO_FIT")
        # best case leaves under 0.4 MB before lawn, sound or music
        margin = pvz_fit.LEAN_FREE - m["best_total"]
        self.assertLess(margin, 400_000)
        lines = "\n".join(pvz_fit.plan_lines(None, pvz_fit.LEAN_FREE, pvz_fit.PROBE_LARGEST))
        self.assertIn("best case", lines)
        self.assertIn("PLAN_REFUSED", lines)
