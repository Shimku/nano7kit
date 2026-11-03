#!/usr/bin/env python3
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.app_catalog import catalog, reset_catalog_cache, resolve_name
from host.compat import Refused
from host.fruitninja import (
    FRUIT_BUNDLE_ID,
    FRUIT_CMD,
    FRUIT_IPA_SHA256,
    STUBS,
    catalog_archive,
    decode_rgba,
    parse_macho,
    print_inventory,
    render_host_png,
    write_nano_data,
    write_synthetic_archive,
)
from host.ports import FRUIT_BUNDLE_ID as PORTS_BUNDLE, missing_help, prepare_for_add

ROOT = Path(__file__).resolve().parent.parent


def _minimal_macho(cryptid=0) -> bytes:
    """Tiny 32-bit ARMv6 Mach-O with LC_ENCRYPTION_INFO cryptid 0 and a dylib."""
    # header 28 + encryption 20 + dylib padded
    ncmds = 2
    enc = struct.pack("<IIIIII", 0x21, 20, 4096, 100, cryptid, 0)[:20]
    # LC_LOAD_DYLIB: cmd, cmdsize, name_off, timestamp, current, compat
    name = b"/System/Library/Frameworks/OpenAL.framework/OpenAL\x00"
    # align cmdsize to 4
    name_off = 24
    pad = (4 - ((name_off + len(name)) % 4)) % 4
    dylib_payload = struct.pack("<IIIIII", 0x0C, name_off + len(name) + pad, name_off, 0, 0, 0)
    # wait LC_LOAD_DYLIB is cmd, cmdsize, then lc_str offset, timestamp, current, compat
    # struct dylib_command { load_command; dylib { name, timestamp, current, compat } }
    cmdsize = 24 + len(name) + pad
    dylib = struct.pack("<II", 0x0C, cmdsize) + struct.pack("<IIII", 24, 0, 0, 0) + name + (b"\x00" * pad)
    sizeofcmds = len(enc) + len(dylib)
    hdr = struct.pack(
        "<IIIIIII",
        0xFEEDFACE,
        12,  # CPU_TYPE_ARM
        6,   # ARMv6
        2,   # MH_EXECUTE
        ncmds,
        sizeofcmds,
        0,
    )
    return hdr + enc + dylib


class FruitCatalogTests(unittest.TestCase):
    def setUp(self):
        reset_catalog_cache()

    def tearDown(self):
        reset_catalog_cache()

    def test_alias_and_catalog(self):
        self.assertEqual(resolve_name("fruit"), "Fruit Ninja")
        self.assertEqual(resolve_name("ninja"), "Fruit Ninja")
        self.assertEqual(resolve_name("Fruit Ninja"), "Fruit Ninja")
        rows = {r["name"]: r for r in catalog()}
        self.assertIn("Fruit Ninja", rows)
        self.assertTrue(rows["Fruit Ninja"]["needs_payload"])
        self.assertTrue(rows["Fruit Ninja"]["large_gl"])   # the recompile needs slot B
        self.assertIn("427538e7", missing_help("Fruit Ninja"))
        self.assertIn(FRUIT_CMD, missing_help("Fruit Ninja"))
        self.assertEqual(PORTS_BUNDLE, FRUIT_BUNDLE_ID)

    def test_prepare_without_payload_refuses(self):
        from host.ports import extra_data_dir

        data = extra_data_dir("Fruit Ninja")
        hidden = None
        if data is not None and data.is_dir():
            hidden = data.with_name("FruitNinja.hidden-test")
            if hidden.exists():
                import shutil

                shutil.rmtree(hidden)
            data.rename(hidden)

            def _restore():
                if hidden is not None and hidden.exists() and not data.exists():
                    hidden.rename(data)

            self.addCleanup(_restore)
        reset_catalog_cache()
        with self.assertRaises(Refused) as ctx:
            prepare_for_add("Fruit Ninja")
        self.assertIn("Fruit Ninja", str(ctx.exception))
        self.assertIn("payload", str(ctx.exception).lower())

    def test_stubs_cover_openal_and_openfeint(self):
        names = {s["name"] for s in STUBS}
        self.assertIn("OpenAL", names)
        self.assertIn("OpenFeint", names)
        self.assertIn("Facebook", names)
        self.assertNotIn("\u2014", json.dumps(STUBS))


class ArchiveStreamTests(unittest.TestCase):
    def test_synthetic_catalog_does_not_need_8mb(self):
        with tempfile.TemporaryDirectory() as raw:
            arc = write_synthetic_archive(Path(raw) / "fileArcive.bin")
            recs = catalog_archive(arc)
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[0]["width"], 16)
            self.assertEqual(recs[1]["width"], 32)
            self.assertFalse(recs[0]["pvrtc"])
            # header+payload for 16x16: 12+512 = 524; then 32x32: 12+2048
            self.assertEqual(recs[0]["offset"], 0)
            self.assertEqual(recs[1]["offset"], 12 + 16 * 16 * 2)

    def test_decode_4444_and_host_png(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            red = bytes([0x0F, 0xF0]) * (16 * 16)  # RGBA4444-ish
            arc = td / "a.bin"
            blob = bytes([4, 4, 0x10, 0]) + struct.pack("<HH I", 16, 16, 0xFFFFFFFF) + red
            arc.write_bytes(blob)
            recs = catalog_archive(arc)
            from host.fruitninja import extract_textures, read_tex_pixels

            pix = read_tex_pixels(arc, recs[0])
            rgba = decode_rgba(pix, 16, 16, 0x10)
            self.assertEqual(len(rgba), 256)
            written = extract_textures(arc, td / "tex")
            self.assertTrue(written[0]["png"])
            frame = render_host_png(written, td / "frame.png")
            self.assertTrue(frame.is_file())
            self.assertGreater(frame.stat().st_size, 100)
            staged = write_nano_data(written, td / "nano")
            self.assertTrue((td / "nano" / "banner.rgb565").is_file())
            self.assertTrue((td / "nano" / "catalog.json").is_file())
            self.assertIn("not copied", (td / "nano" / "README.txt").read_text())
            self.assertGreater(staged["bytes"], 0)

    def test_pvrtc_skipped(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            payload = b"\x00" * (8 * 8 * 2)
            blob = bytes([3, 3, 0x00, 0]) + struct.pack("<HH I", 8, 8, 0xFFFFFFFF) + payload
            (td / "a.bin").write_bytes(blob)
            recs = catalog_archive(td / "a.bin")
            self.assertTrue(recs[0]["pvrtc"])
            from host.fruitninja import extract_textures

            written = extract_textures(td / "a.bin", td / "tex")
            self.assertEqual(written[0]["skipped"], "pvrtc")
            self.assertEqual(written[0]["png"], "")


class MachOInventoryTests(unittest.TestCase):
    def test_thin_armv6_cryptid0(self):
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "Fruit"
            p.write_bytes(_minimal_macho(0))
            info = parse_macho(p)
            self.assertEqual(info["cryptid"], 0)
            self.assertTrue(info["armv6"])
            self.assertTrue(info["has_openal"])
            self.assertFalse(info["has_uikit"])

    def test_refuses_not_macho(self):
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "Fruit"
            p.write_bytes(b"not macho")
            with self.assertRaises(Refused):
                parse_macho(p)


class PrintInventoryTests(unittest.TestCase):
    def test_print_without_payload(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = print_inventory()
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("OpenAL", text)
        self.assertIn("OpenFeint", text)
        self.assertIn("fileArcive.bin", text)
        self.assertIn(FRUIT_CMD, text)
        self.assertNotIn("\u2014", text)
        self.assertIn(FRUIT_IPA_SHA256[:16], missing_help("Fruit Ninja") + FRUIT_IPA_SHA256)


class GitignoreTests(unittest.TestCase):
    def test_derived_data_ignored_ipa_ignored(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.ipa", gi)
        self.assertIn("untether/artifacts/extra/Data/FruitNinja/", gi)
        # the hbapp holds translated game code, so it is derived too
        self.assertIn("untether/artifacts/extra/Executables/Fruit Ninja.hbapp", gi)


if __name__ == "__main__":
    unittest.main()
