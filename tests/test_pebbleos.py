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
        self.assertIn("cortex-a5", mk)
        self.assertIn("HB_CPU", mk)

    def test_apache_notice_and_no_vendor(self):
        notice = (APP / "NOTICE").read_text()
        self.assertIn("Apache License 2.0", notice)
        self.assertIn("does not vendor", notice)
        readme = (APP / "README.md").read_text()
        self.assertIn("fetched at build time", readme)
        self.assertIn("./start nabase add", readme)
        self.assertIn("not `./start apps add`", readme)

    def test_docs_list_device_like_tests(self):
        doc = DOC.read_text()
        self.assertIn("no host libc unwinder", doc)
        self.assertIn("vfpv4-d16", doc)
        self.assertIn("3.9 MB", doc)
        self.assertIn("2 MB", doc)
        self.assertIn("mkrelocapp", doc)
        self.assertIn("/Apps/Data/PebbleOS", doc)

    def test_no_em_dashes(self):
        for path in (APP / "pebbleos.c", APP / "README.md", APP / "emu.py", APP / "host_hal.c",
                     DOC, ROOT / "host" / "pebbleos.py"):
            text = path.read_text()
            self.assertNotIn("—", text, msg=str(path))

    def test_pinned_commit(self):
        src = (ROOT / "host" / "pebbleos.py").read_text()
        self.assertIn("94e3d98daa0dfd6bd204ca1973f1b502eed07d5f", src)
        self.assertIn("https://github.com/coredevices/PebbleOS", src)
        self.assertIn("Apache-2.0", src)




class TestPebbleosImage(unittest.TestCase):
    """Device-like checks of the built .hbapp, when it is present."""

    hbapp = APP / "build" / "pebbleos.hbapp"
    elf = APP / "build" / "pebbleos.reloc.elf"

    @unittest.skipUnless(hbapp.is_file(), "needs ./start pebbleos build")
    def test_hbapp_is_mkreloc_and_under_2mb(self):
        import struct
        data = self.hbapp.read_bytes()
        magic, entry, image, span, count, align = struct.unpack_from("<6I", data)
        self.assertEqual(magic, 0x314C5248)  # HRL1
        self.assertEqual(align, 16)
        self.assertGreater(image, 200 * 1024)
        self.assertGreater(count, 100)
        # The loader allocates `span` as one block; stay well under 2 MB.
        self.assertLess(span, 2 * 1024 * 1024)
        self.assertLess(span, 1600 * 1024)
        self.assertEqual(len(data), 24 + image + count * 4)

    @unittest.skipUnless(elf.is_file(), "needs ./start pebbleos build")
    def test_no_host_libc_unwinder_tables(self):
        import subprocess
        out = subprocess.check_output(["arm-none-eabi-readelf", "-S", str(self.elf)], text=True)
        self.assertNotIn(".ARM.exidx", out)
        self.assertNotIn(".ARM.extab", out)

    @unittest.skipUnless(elf.is_file(), "needs ./start pebbleos build")
    def test_cortex_a5_vfpv4_d16(self):
        import subprocess
        # gcc 15 with -mcpu=cortex-a5 -mfloat-abi=soft writes Tag_CPU_name
        # "7-A" (the A5 is an ARMv7-A core) and omits Tag_FP_arch because
        # no VFP instructions are emitted. The Makefile still names the
        # device CPU and FPU so the image is not built for A8/NEON.
        out = subprocess.check_output(["arm-none-eabi-readelf", "-A", str(self.elf)], text=True)
        self.assertIn("Tag_CPU_arch: v7", out)
        self.assertIn("Application", out)
        self.assertIn("Thumb-2", out)
        self.assertNotIn("Advanced SIMD", out)
        mk = (APP / "Makefile").read_text()
        self.assertIn("cortex-a5", mk)
        self.assertIn("vfpv4-d16", mk)
        self.assertIn("soft", mk)

    @unittest.skipUnless(hbapp.is_file(), "needs ./start pebbleos build")
    def test_low_memory_screen_240x432(self):
        import subprocess, tempfile
        from PIL import Image
        emu = APP / "emu.py"
        self.assertTrue(emu.is_file())
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "script"
            script.write_text("1\nshot lowmem\n")
            r = subprocess.run(
                ["python3", str(emu), str(self.hbapp), td, str(script), "--largest", "262144"],
                check=True, capture_output=True, text=True,
            )
            png = Path(td) / "lowmem.png"
            self.assertTrue(png.is_file(), msg=r.stdout + r.stderr)
            im = Image.open(png)
            self.assertEqual(im.size, (240, 432))
            kind = (Path(td) / "lowmem.kind").read_text().strip()
            self.assertEqual(kind, "low-memory")


if __name__ == "__main__":
    unittest.main()
