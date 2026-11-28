# SPDX-License-Identifier: MIT
"""Checks for the hosted PebbleOS nano app (shim, memory screen, notices)."""

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "nanoapps" / "apps" / "pebbleos"
DOC = ROOT / "docs" / "PEBBLEOS.md"


class TestPebbleosShim(unittest.TestCase):
    def test_low_memory_screen_comes_before_host_start(self):
        src = (APP / "pebbleos.c").read_text()
        self.assertIn("NOT ENOUGH MEMORY", src)
        self.assertLess(src.index("NOT ENOUGH MEMORY"), src.index("pebbleos_host_start"))
        self.assertIn("PBL_NEED_LARGEST", src)
        # Do not start the kernel when the floor is missed.
        self.assertIn("s_low_mem", src)

    def test_button_map(self):
        src = (APP / "pebbleos.c").read_text()
        self.assertIn("HB_BTN_HOME", src)
        self.assertIn("HB_BTN_PLAY_PAUSE", src)
        self.assertIn("HB_BTN_VOL_UP", src)
        self.assertIn("HB_BTN_VOL_DOWN", src)
        self.assertIn("PBL_KEY_BACK", src)
        self.assertIn("PBL_KEY_SELECT", src)
        # Sleep/Wake is not used as Select.
        self.assertNotIn("HB_BTN_POWER", src)

    def test_panel_is_pebble_size_centred_on_nano(self):
        header = (APP / "pebbleos_host.h").read_text()
        self.assertIn("#define PBL_PANEL_W 200", header)
        self.assertIn("#define PBL_PANEL_H 228", header)
        src = (APP / "pebbleos.c").read_text()
        self.assertIn("#define SCREEN_W 240", src)
        self.assertIn("#define SCREEN_H 432", src)
        self.assertIn("panel_x", src)

    def test_device_like_fpu_flags(self):
        mk = (APP / "Makefile").read_text()
        self.assertIn("vfpv4-d16", mk)
        self.assertIn("HB_FPU", mk)

    def test_apache_notice_and_no_vendor(self):
        notice = (APP / "NOTICE").read_text()
        self.assertIn("Apache License 2.0", notice)
        self.assertIn("does not vendor", notice)
        readme = (APP / "README.md").read_text()
        self.assertIn("fetched at build time", readme)
        self.assertIn("./start nabase add", readme)
        self.assertIn("not `./start apps add`", readme)

    def test_docs_do_not_call_it_ready(self):
        doc = DOC.read_text()
        self.assertIn("What is left before it is called ready", doc)
        self.assertIn("no host libc unwinder", doc)
        self.assertIn("vfpv4-d16", doc)
        self.assertIn("3.9 MB", doc)
        self.assertIn("2 MB", doc)

    def test_no_em_dashes(self):
        for path in (APP / "pebbleos.c", APP / "README.md", DOC, ROOT / "host" / "pebbleos.py"):
            text = path.read_text()
            self.assertNotIn("—", text, msg=str(path))

    def test_pinned_commit(self):
        src = (ROOT / "host" / "pebbleos.py").read_text()
        self.assertIn("94e3d98daa0dfd6bd204ca1973f1b502eed07d5f", src)
        self.assertIn("https://github.com/coredevices/PebbleOS", src)
        self.assertIn("Apache-2.0", src)


if __name__ == "__main__":
    unittest.main()
