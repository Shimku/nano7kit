# SPDX-License-Identifier: MIT
"""Checks for the hosted PebbleOS nano app (kernel host, memory screen, notices)."""

from pathlib import Path
import os
import sys
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
            self.assertNotIn("\u2014", text, msg=str(path))

    def test_pinned_commit(self):
        src = (ROOT / "host" / "pebbleos.py").read_text()
        self.assertIn("94e3d98daa0dfd6bd204ca1973f1b502eed07d5f", src)
        self.assertIn("https://github.com/coredevices/PebbleOS", src)
        self.assertIn("Apache-2.0", src)


def _tool(name):
    """An arm-none-eabi binutil from PATH or the usual toolchain folders."""
    import shutil
    found = shutil.which(name)
    if found:
        return found
    for base in (Path.home() / "pebbleos-sdk-0.1.17" / "arm-none-eabi" / "bin",
                 Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        if (base / name).is_file():
            return str(base / name)
    return None


HBAPP = APP / "build" / "pebbleos.hbapp"
ELF = APP / "build" / "pebbleos.reloc.elf"
EMU = APP / "emu.py"
DATA = ROOT / "untether" / "artifacts" / "extra" / "Data" / "PebbleOS"
TIGHT = "2.0,1.0,0.9"   # 3.9 MB free, 2 MB largest block, before the image is placed
ROOMY = "4.0,1.0,1.0"   # 6 MB free, 4 MB largest block


class TestPebbleosImage(unittest.TestCase):
    """Static checks of the device .hbapp, when it is built."""

    @unittest.skipUnless(HBAPP.is_file(), "needs ./start sdk build pebbleos")
    def test_hbapp_is_mkreloc_and_under_2mb(self):
        import struct
        data = HBAPP.read_bytes()
        magic, entry, image, span, count, align = struct.unpack_from("<6I", data)
        self.assertEqual(magic, 0x314C5248)  # HRL1
        self.assertEqual(align, 16)
        self.assertGreater(image, 400 * 1024)
        self.assertGreater(count, 100)
        # The loader allocates `span` as one block; stay well under 2 MB.
        self.assertLess(span, 1600 * 1024)
        self.assertEqual(len(data), 24 + image + count * 4)

    @unittest.skipUnless(ELF.is_file(), "needs ./start sdk build pebbleos")
    def test_real_kernel_is_linked(self):
        blob = ELF.read_bytes()
        for sym in (b"pbl_nano_kernel_start", b"pbl_posix_start", b"launcher_menu_app_get_app_info"):
            self.assertIn(sym, blob)

    @unittest.skipUnless(ELF.is_file() and _tool("arm-none-eabi-readelf"), "needs the build and readelf")
    def test_no_host_libc_unwinder(self):
        import subprocess
        out = subprocess.check_output([_tool("arm-none-eabi-readelf"), "-S", "-s", "-W", str(ELF)], text=True)
        self.assertNotIn(".ARM.exidx", out)
        self.assertNotIn(".ARM.extab", out)
        for sym in ("_Unwind_", "__gxx_personality", "__aeabi_unwind_cpp"):
            self.assertNotIn(sym, out)

    @unittest.skipUnless(ELF.is_file() and _tool("arm-none-eabi-objdump"), "needs the build and objdump")
    def test_cortex_a5_vfpv4_d16(self):
        import re
        import subprocess
        # Built -mcpu=cortex-a5 -mfpu=vfpv4-d16 -mfloat-abi=soft, so the
        # image must not touch NEON or d16-d31 (absent on the nano's A5).
        # Soft float emits no VFP code at all, which is checked here too.
        attrs = subprocess.check_output([_tool("arm-none-eabi-readelf"), "-A", str(ELF)], text=True)
        self.assertIn("Tag_CPU_arch: v7", attrs)
        self.assertIn("Application", attrs)
        self.assertIn("Thumb-2", attrs)
        self.assertNotIn("Advanced SIMD", attrs)
        dis = subprocess.check_output([_tool("arm-none-eabi-objdump"), "-d", str(ELF)], text=True)
        high = re.compile(r"\b(d(1[6-9]|2[0-9]|3[01])|q[0-9]+)\b")
        # objdump: "addr:<TAB>hex<TAB>mnemonic<TAB>operands". Data dumps have
        # no second tab, so they are not mistaken for instructions.
        insn = re.compile(r"^\s*[0-9a-f]+:\t[0-9a-f ]+\t([a-z][a-z0-9.]*)(.*)$", re.M)
        found = insn.findall(dis)
        self.assertGreater(len(found), 100000)
        vfp = [m + ops for m, ops in found if m.startswith("v")]
        self.assertEqual([v for v in vfp if high.search(v)], [])
        self.assertEqual(vfp, [])
        mk = (APP / "Makefile").read_text()
        for flag in ("cortex-a5", "vfpv4-d16", "soft"):
            self.assertIn(flag, mk)


class TestPebbleosInstall(unittest.TestCase):
    """What `./start nabase add PebbleOS` copies."""

    def test_port_data_goes_to_apps_data(self):
        sys.path.insert(0, str(ROOT))
        from host.ports import PORT_DATA, LARGE_GL
        self.assertEqual(PORT_DATA["PebbleOS"]["fat"], "/Apps/Data/PebbleOS")
        self.assertTrue(PORT_DATA["PebbleOS"]["required"])
        self.assertIn("PebbleOS", LARGE_GL)

    @unittest.skipUnless((DATA / "flash" / "00.bin").is_file(), "needs ./start sdk build pebbleos")
    def test_only_resource_banks_are_copied(self):
        sys.path.insert(0, str(ROOT))
        from host.ports import data_files
        files = data_files("PebbleOS")
        self.assertTrue(files)
        for f in files:
            self.assertEqual(f.parent.name, "flash", f)
            self.assertEqual(f.stat().st_size, 65536, f)
            # File system banks start at 0x20; a reinstall must not touch them.
            self.assertLess(int(f.stem, 16), 0x20, f)


def _run(td, name, script, os_blocks=None, fs=None, largest=None, save=None):
    import subprocess
    out = Path(td) / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "script").write_text(script)
    cmd = [sys.executable, str(EMU), str(HBAPP), str(out), str(out / "script")]
    if os_blocks:
        cmd += ["--os-blocks", os_blocks]
    if largest:
        cmd += ["--largest", str(largest)]
    if fs is not None:
        cmd += ["--fs-dir", str(fs)]
    if save is not None:
        cmd += ["--save-fs", str(save)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _copy_data(dest):
    import shutil
    shutil.copytree(DATA, dest)
    return dest


@unittest.skipUnless(HBAPP.is_file() and (DATA / "flash" / "00.bin").is_file(),
                     "needs ./start sdk build pebbleos")
class TestPebbleosBoot(unittest.TestCase):
    """Unicorn runs of the device .hbapp: the image is loaded the way the
    launcher loads it, with the OS heap at both memory levels. The scenarios
    run in parallel once for the whole class."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        flash = lambda d: d / "flash"
        fs_tight = _copy_data(td / "fs-tight")
        fs_roomy = _copy_data(td / "fs-roomy")
        fs_badres = _copy_data(td / "fs-badres")
        p = flash(fs_badres) / "05.bin"
        b = bytearray(p.read_bytes())
        b[100:4100] = b"\x5a" * 4000
        p.write_bytes(b)
        fs_short = _copy_data(td / "fs-short")
        (flash(fs_short) / "05.bin").unlink()
        fs_corrupt = _copy_data(td / "fs-corrupt")
        for bank in range(0x20, 0x24):
            # The file system partition starts at 2 MiB (bank 0x20).
            (flash(fs_corrupt) / f"{bank:02x}.bin").write_bytes(b"\xa5" * 65536)
        face = "40\nshot face\n"
        runs = {
            # Resources only, nothing saved yet: the first boot.
            "tight": _run(td, "tight", face + "relaunch\n40\nshot relaunch\n", TIGHT, fs_tight,
                          save=td / "fs-saved"),
            # The launcher opens with an animation and takes no keys until it
            # has finished, so wait 100 frames (2 s of emulated time).
            "roomy": _run(td, "roomy", face + "click select\n100\nshot launcher\n"
                          "click back\n60\nshot back\n",
                          ROOMY, fs_roomy),
            "noflash": _run(td, "noflash", "4\nshot face\n", TIGHT, None),
            "short": _run(td, "short", "4\nshot face\n", TIGHT, fs_short),
            "badres": _run(td, "badres", face, TIGHT, fs_badres),
            "corrupt": _run(td, "corrupt", face, TIGHT, fs_corrupt),
            "lowmem": _run(td, "lowmem", "1\nshot face\n", None, None, largest=262144),
        }
        cls.out = {}
        for name, proc in runs.items():
            cls.out[name] = proc.communicate()[0]
            cls.out[name + ".rc"] = proc.returncode
        # Second boot from the file system the first boot left behind.
        proc = _run(td, "second", face, ROOMY, td / "fs-saved")
        cls.out["second"] = proc.communicate()[0]
        cls.out["second.rc"] = proc.returncode
        shots = os.environ.get("PEBBLEOS_SHOTS")
        if shots:
            import shutil
            for name in ("tight/face", "roomy/launcher", "noflash/face", "badres/face", "lowmem/face"):
                src = td / f"{name}.png"
                if src.is_file():
                    shutil.copy2(src, Path(shots) / (name.replace("/", "-") + ".png"))
        cls.td = td

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def kind(self, run, shot):
        path = self.td / run / f"{shot}.kind"
        self.assertTrue(path.is_file(), msg=self.out.get(run))
        from PIL import Image
        with Image.open(self.td / run / f"{shot}.png") as im:
            self.assertEqual(im.size, (240, 432))
        return path.read_text().strip()

    def test_tight_memory_boots_to_watchface(self):
        self.assertEqual(self.out["tight.rc"], 0, self.out["tight"])
        self.assertIn("largest 1,048,576", self.out["tight"])
        self.assertEqual(self.kind("tight", "face"), "watchface", self.out["tight"])

    def test_roomy_memory_boots_to_watchface(self):
        self.assertEqual(self.out["roomy.rc"], 0, self.out["roomy"])
        self.assertEqual(self.kind("roomy", "face"), "watchface", self.out["roomy"])

    def test_buttons_open_launcher_and_go_back(self):
        # Play/Pause (Select) opens the launcher, Home (Back) closes it.
        self.assertEqual(self.kind("roomy", "launcher"), "launcher", self.out["roomy"])
        self.assertEqual(self.kind("roomy", "back"), "watchface", self.out["roomy"])

    def test_relaunch_does_not_leak(self):
        # Opening the app again frees the old aux block before taking a new one.
        self.assertEqual(self.kind("tight", "relaunch"), "watchface", self.out["tight"])
        self.assertIn("OS heap: blocks held 1 ", self.out["tight"])
        self.assertIn("aux block: set", self.out["tight"])

    def test_no_save_first_boot_writes_little(self):
        import re
        m = re.search(r"data.fs: (\d+) files written \(([\d,]+) bytes\)", self.out["tight"])
        self.assertIsNotNone(m, self.out["tight"])
        # Formatting the file system, then the relaunch. Banks are written
        # once per frame at most, not once per kernel write.
        self.assertLess(int(m.group(2).replace(",", "")), 8 * 1024 * 1024)

    def test_second_boot_reads_saved_state(self):
        import re
        self.assertEqual(self.kind("second", "face"), "watchface", self.out["second"])
        m = re.search(r"data.fs: (\d+) files written", self.out["second"])
        written = int(m.group(1)) if m else 0
        self.assertLessEqual(written, 8, self.out["second"])

    def test_corrupt_save_still_boots(self):
        self.assertEqual(self.kind("corrupt", "face"), "watchface", self.out["corrupt"])

    def test_no_flash_files_shows_resources_screen(self):
        self.assertEqual(self.kind("noflash", "face"), "no-resources", self.out["noflash"])
        self.assertIn("blocks held 0", self.out["noflash"])

    def test_missing_resource_bank_shows_resources_screen(self):
        self.assertEqual(self.kind("short", "face"), "no-resources", self.out["short"])

    def test_corrupt_resources_stop_cleanly(self):
        # PebbleOS finds the CRC mismatch and asks to reset; the app shows
        # a plain screen instead of a frozen panel.
        self.assertIn(self.kind("badres", "face"), ("stopped",), self.out["badres"])

    def test_low_memory_screen(self):
        self.assertEqual(self.kind("lowmem", "face"), "low-memory", self.out["lowmem"])
        self.assertIn("blocks held 0", self.out["lowmem"])


if __name__ == "__main__":
    unittest.main()
