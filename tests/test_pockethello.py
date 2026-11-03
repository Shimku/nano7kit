#!/usr/bin/env python3
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.app_catalog import catalog, reset_catalog_cache, resolve_name

ROOT = Path(__file__).resolve().parent.parent
START = ROOT / "start"


class PocketHelloTests(unittest.TestCase):
    def setUp(self):
        reset_catalog_cache()

    def tearDown(self):
        reset_catalog_cache()

    def test_alias_and_catalog(self):
        self.assertEqual(resolve_name("jshello"), "JS Hello")
        self.assertEqual(resolve_name("pockethello"), "JS Hello")
        self.assertEqual(resolve_name("JS Hello"), "JS Hello")
        rows = {r["name"]: r for r in catalog()}
        self.assertIn("JS Hello", rows)
        row = rows["JS Hello"]
        self.assertEqual(row["bundle_id"], "org.nano7kit.pockethello")
        self.assertEqual(row["category"], "first-party")
        self.assertEqual(row["kind"], 1)
        self.assertFalse(row["needs_payload"])
        self.assertFalse(row["large_gl"])
        self.assertIn("Not the PocketJS runtime", row["author"])
        extra = ROOT / "untether" / "artifacts" / "extra"
        hb = extra / "Executables" / "JS Hello.hbapp"
        icon = extra / "Icons" / "JS Hello.bin"
        self.assertTrue(hb.is_file())
        self.assertTrue(icon.is_file())
        self.assertGreater(hb.stat().st_size, 4000)
        self.assertLess(hb.stat().st_size, 20_000)
        self.assertEqual(icon.stat().st_size, 50208)
        self.assertTrue(row["present"])

    def test_hal_header_is_240x432(self):
        text = (ROOT / "nanoapps" / "sdk" / "pocket_hal.h").read_text(encoding="utf-8")
        self.assertIn("#define POCKET_HAL_W 240", text)
        self.assertIn("#define POCKET_HAL_HEIGHT 432", text)
        self.assertIn("176x132", text)
        self.assertIn("not a click wheel", text.lower())
        self.assertNotIn("\u2014", text)
        src = (ROOT / "nanoapps" / "apps" / "pockethello" / "pockethello.c").read_text(
            encoding="utf-8"
        )
        self.assertIn("pocket_hal_bind", src)
        self.assertIn("NOT POCKETJS RT", src)
        self.assertIn("NOT NANO 2G 176", src)
        self.assertNotIn("\u2014", src)

    def test_docs_name_the_path(self):
        doc = (ROOT / "docs" / "POCKETJS.md").read_text(encoding="utf-8")
        for needle in (
            "176x132",
            "240x432",
            "input.buttons",
            "MicroTS",
            "pocket_hal.h",
            "./start pocketjs hello",
            "JS Hello",
            "not the PocketJS runtime",
            "will not",
        ):
            self.assertIn(needle, doc)
        self.assertNotIn("\u2014", doc)


class PocketjsCliTests(unittest.TestCase):
    def test_status_lists_hello_and_refuses_runtime(self):
        r = subprocess.run(
            [sys.executable, str(START), "pocketjs"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        blob = r.stdout
        for needle in (
            "MIT",
            "ipodnano",
            "176x132",
            "240x432",
            "will not",
            "./start pocketjs clone",
            "./start pocketjs hello",
            "JS Hello",
            "Rockbox",
            "not vendored",
            "QuickJS",
        ):
            self.assertIn(needle, blob)
        self.assertNotIn("\u2014", blob)
        self.assertIn("will not", blob.lower())

    def test_help_lists_hello(self):
        r = subprocess.run(
            [sys.executable, str(START), "pocketjs", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello", r.stdout)


if __name__ == "__main__":
    unittest.main()
