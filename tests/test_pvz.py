#!/usr/bin/env python3
import os
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused
from host.pvz import (
    PVZ_BUNDLE_ID,
    PVZ_CMD,
    PVZ_EXE_SIZE,
    PVZ_IPA_SHA256,
    PVZ_TEXT_SIZE,
    SLOT_A,
    STUBS,
    catalog_ipa,
    classify_macho,
    print_inventory,
    render_host_png,
    slot_budget,
)
from host.fruitninja import parse_macho

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_IPA = Path(os.environ.get("NANO7KIT_PVZ_IPA", "/nonexistent/pvz.ipa"))


def _minimal_macho(cryptid=0) -> bytes:
    enc = struct.pack("<IIIIII", 0x21, 20, 4096, 100, cryptid, 0)[:20]
    name = b"/System/Library/Frameworks/UIKit.framework/UIKit\x00"
    pad = (4 - ((24 + len(name)) % 4)) % 4
    cmdsize = 24 + len(name) + pad
    dylib = (
        struct.pack("<II", 0x0C, cmdsize)
        + struct.pack("<IIII", 24, 0, 0, 0)
        + name
        + (b"\x00" * pad)
    )
    sizeofcmds = len(enc) + len(dylib)
    hdr = struct.pack(
        "<IIIIIII",
        0xFEEDFACE,
        12,
        6,
        2,
        2,
        sizeofcmds,
        0,
    )
    return hdr + enc + dylib


def _png(w=8, h=8) -> bytes:
    import zlib

    raw = b"".join(b"\x00" + (b"\xff\x00\x00" * w) for _ in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class PvzPrintTests(unittest.TestCase):
    def test_print_without_payload(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = print_inventory()
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        for needle in (
            "OpenAL",
            "FMOD",
            "UIKit",
            "MediaPlayer",
            "SexyApp",
            "slot B",
            "576",
            PVZ_CMD,
            PVZ_IPA_SHA256[:16],
            "research only",
            "No apps add",
        ):
            self.assertIn(needle, text)
        self.assertNotIn("\u2014", text)
        self.assertNotIn("apps add 'PvZ'", text)

    def test_stubs_cover_fmod_uikit_movie(self):
        names = {s["name"] for s in STUBS}
        self.assertTrue(any("FMOD" in n for n in names))
        self.assertTrue(any("UIKit" in n for n in names))
        self.assertTrue(any("MediaPlayer" in n for n in names))
        self.assertNotIn("\u2014", json.dumps(STUBS))


class PvzBudgetTests(unittest.TestCase):
    def test_text_forces_slot_b(self):
        info = slot_budget(PVZ_EXE_SIZE, PVZ_TEXT_SIZE, 33_091_628)
        self.assertEqual(info["slot"], "B")
        self.assertFalse(info["exe_fits_slot_a"])
        self.assertFalse(info["text_fits_slot_a"])
        self.assertGreater(PVZ_TEXT_SIZE, SLOT_A)
        self.assertGreater(PVZ_EXE_SIZE, SLOT_A)
        self.assertIn("576", info["why_slot_b"])

    def test_host_png_procedural(self):
        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw) / "frame.png"
            out = render_host_png(dest)
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 100)


class PvzCatalogTests(unittest.TestCase):
    def test_zip_catalog_skips_mp4_body(self):
        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            ipa = td / "pvz.ipa"
            with zipfile.ZipFile(ipa, "w") as zf:
                zf.writestr("Payload/PvZ.app/images/tiny.png", _png(16, 8))
                zf.writestr("Payload/PvZ.app/video/credits.mp4", b"0" * 64)
                zf.writestr("Payload/PvZ.app/music/day.caf", b"caf")
                zf.writestr(
                    "Payload/PvZ.app/compiled/award.xml.compiled",
                    struct.pack("<II", 0xDEADFED4, 4) + b"xxxx",
                )
            cat = catalog_ipa(ipa)
            self.assertEqual(cat["png_count"], 1)
            self.assertEqual(cat["pngs"][0]["width"], 16)
            self.assertEqual(cat["pngs"][0]["height"], 8)
            self.assertEqual(cat["pngs"][0]["rgba"], 16 * 8 * 4)
            self.assertEqual(cat["caf_count"], 1)
            self.assertEqual(cat["compiled_count"], 1)
            self.assertEqual(cat["mp4_bytes"], 64)
            self.assertIn("Skip credits.mp4", cat["plan"])

    def test_macho_flags_uikit_not_openal(self):
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "PvZ"
            p.write_bytes(_minimal_macho(0))
            info = parse_macho(p)
            self.assertEqual(info["cryptid"], 0)
            self.assertTrue(info["armv6"])
            self.assertTrue(info["has_uikit"])
            classified = classify_macho(p)
            self.assertTrue(classified["has_uikit_dylib"])
            self.assertFalse(classified["has_openal_dylib"])
            self.assertFalse(classified["has_fmod_dylib"])


class PvzLiveDumpTests(unittest.TestCase):
    @unittest.skipUnless(
        REFERENCE_IPA.is_file(),
        "set NANO7KIT_PVZ_IPA to run",
    )
    def test_reference_dump_matches_pinned(self):
        from host.compat import sha256_file

        digest = sha256_file(REFERENCE_IPA)
        self.assertEqual(digest, PVZ_IPA_SHA256)
        cat = catalog_ipa(REFERENCE_IPA)
        self.assertEqual(cat["members"], 507)
        self.assertEqual(cat["png_count"], 70)
        self.assertEqual(cat["caf_count"], 173)
        self.assertEqual(cat["compiled_count"], 218)
        self.assertEqual(cat["mp4_bytes"], 12_074_127)
        self.assertGreater(cat["png_rgba"], 30_000_000)
        atlas = next(p for p in cat["pngs"] if p["name"] == "PlantsZombies.png")
        self.assertEqual(atlas["width"], 1024)
        self.assertEqual(atlas["height"], 1003)
        self.assertTrue(atlas["over_512"])


class GitignoreTests(unittest.TestCase):
    def test_pvz_data_and_ipa_ignored(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.ipa", gi)
        self.assertIn("untether/artifacts/extra/Data/PvZ/", gi)


if __name__ == "__main__":
    unittest.main()
