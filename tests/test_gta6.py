#!/usr/bin/python3
"""GTA 6 NanoApp: catalog name, memory plan, host play, contact sheet."""
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host import gta6 as g6  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "nanoapps" / "apps" / "gta6"
HOST_BIN = APP / "build" / "gta6_host"
CONTACT = ROOT / "docs" / "images" / "gta6-contact.png"
FORBIDDEN = (
    "Vice City", "Los Santos", "Liberty City", "Leonida",
    "Lucia", "Jason", "Pricedown", "Rockstar",
)
EM = "\u2014"


def write_png(path: Path, w: int, h: int, rgb: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b""
    stride = w * 3
    for y in range(h):
        raw += b"\x00" + rgb[y * stride : (y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def read_ppm(path: Path):
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        raise ValueError(path)
    header, body = data.split(b"\n255\n", 1)
    parts = header.split()
    w, h = int(parts[1]), int(parts[2])
    return w, h, body


def contact_sheet(ppms, dest: Path) -> None:
    frames = [read_ppm(p) for p in ppms]
    w, h = frames[0][0], frames[0][1]
    n = len(frames)
    out = bytearray(w * n * h * 3)
    for i, (_, _, rgb) in enumerate(frames):
        for y in range(h):
            src = y * w * 3
            dst = (y * (w * n) + i * w) * 3
            out[dst : dst + w * 3] = rgb[src : src + w * 3]
    write_png(dest, w * n, h, bytes(out))


def build_host() -> Path:
    subprocess.check_call(["make", "-C", str(APP), "host"], stdout=subprocess.DEVNULL)
    return HOST_BIN


def run_host(*args: str, timeout=60) -> str:
    bin_path = HOST_BIN if HOST_BIN.is_file() else build_host()
    r = subprocess.run(
        [str(bin_path), *args],
        cwd=str(APP),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        raise AssertionError(r.stdout + "\n" + r.stderr)
    return r.stdout


class CatalogTests(unittest.TestCase):
    def test_home_name_is_exactly_gta_6(self):
        import plistlib

        from host.app_catalog import ALIASES, catalog, reset_catalog_cache, resolve_name

        info = plistlib.loads((APP / "Info.plist").read_bytes())
        self.assertEqual(info["CFBundleName"], "GTA 6")
        self.assertEqual(info["HBAppKind"], 1)
        self.assertEqual(info["CFBundleExecutable"], "gta6")
        reset_catalog_cache()
        rows = {r["name"]: r for r in catalog()}
        self.assertIn("GTA 6", rows)
        self.assertEqual(rows["GTA 6"]["folder"], "gta6")
        self.assertEqual(rows["GTA 6"]["kind"], 1)
        self.assertTrue(rows["GTA 6"]["buildable"])
        self.assertFalse(rows["GTA 6"].get("needs_payload"))
        self.assertFalse(rows["GTA 6"].get("needs_rom"))
        self.assertEqual(resolve_name("gta6"), "GTA 6")
        self.assertEqual(resolve_name("GTA 6"), "GTA 6")
        self.assertEqual(ALIASES["gta"], "GTA 6")
        self.assertEqual(g6.NAME, "GTA 6")
        self.assertEqual(g6.CMD, "./start nabase add 'GTA 6'")

    def test_prepare_for_add_without_payload(self):
        from host.app_catalog import reset_catalog_cache
        from host.ports import prepare_for_add

        reset_catalog_cache()
        with mock.patch("host.sdkapp.build_from_source") as built:
            # not present: should compile from the Makefile, no rom/payload
            prepare_for_add("GTA 6")
            built.assert_called_once_with("GTA 6", "gta6")

    def test_installer_item(self):
        from host.installer import by_name

        item = by_name("GTA 6")
        self.assertEqual(item.name, "GTA 6")
        self.assertEqual(item.needs, "")
        self.assertFalse(item.tested)


class HygieneTests(unittest.TestCase):
    def test_original_copy_and_no_emdash(self):
        files = list(APP.glob("*.c")) + list(APP.glob("*.h")) + [APP / "Info.plist", APP / "Makefile"]
        files += [ROOT / "host" / "gta6.py"]
        blob = "\n".join(p.read_text(encoding="utf-8") for p in files)
        self.assertNotIn(EM, blob)
        for word in FORBIDDEN:
            self.assertNotIn(word, blob, word)
        self.assertIn("Mangrove Key", blob)
        self.assertIn("Nia Voss", blob)
        self.assertIn("Wade Pike", blob)
        self.assertIn("Finally out. On iPod nano.", blob)
        self.assertIn("GTA 6", blob)
        self.assertIn("NOT ENOUGH MEMORY", blob)
        self.assertIn("YAP:", blob)
        self.assertIn("KEY MAN", blob)

    def test_constants_match(self):
        src = (APP / "gta6_game.h").read_text()
        self.assertIn("#define G6_TICK_MS    30", src)
        self.assertIn("#define G6_BLOCK_MAX  0x40000u", src)
        self.assertIn("#define G6_BLOCK_MIN  0x18000u", src)
        self.assertIn('#define G6_TITLE      "GTA 6"', src)
        self.assertIn('#define G6_DATA_DIR   "/Apps/Data/GTA6"', src)
        self.assertEqual(g6.BLOCK_MAX, 0x40000)
        self.assertEqual(g6.FAT_DATA, "/Apps/Data/GTA6")
        nano = (APP / "gta6.c").read_text()
        self.assertIn("hb_touch_poll_multi", nano)
        self.assertIn("HB_APP_AUX_BLOCK", nano)
        self.assertIn("prepare_unload", nano)
        self.assertIn("log.txt", nano)
        self.assertIn("hb_audio_play_wav", nano)


class MemoryTests(unittest.TestCase):
    def test_both_device_heaps_fit_and_tiny_does_not(self):
        clean = g6.heap_fit(6 * 1024 * 1024, 4 * 1024 * 1024)
        tight = g6.heap_fit(int(3.9 * 1024 * 1024), 2 * 1024 * 1024)
        tiny = g6.heap_fit(400000, 80000)
        self.assertEqual(clean["verdict"], "FITS")
        self.assertEqual(tight["verdict"], "FITS")
        self.assertEqual(tiny["verdict"], "NO_FIT")
        self.assertGreaterEqual(clean["block"], g6.BLOCK_MIN)
        self.assertGreaterEqual(tight["block"], g6.BLOCK_MIN)


class HostPlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_host()

    def test_best_and_worst_heap(self):
        out = run_host("--demo", "--dump", str(tempfile.mkdtemp()))
        self.assertIn("VERDICT=FITS", out)
        out2 = run_host("--tight", "--nosave")
        self.assertIn("VERDICT=FITS", out2)
        out3 = run_host("--tiny")
        self.assertIn("VERDICT=NO_FIT", out3)

    def test_save_corrupt_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            save = d / "save.bin"
            run_host("--save", str(save))
            self.assertGreater(save.stat().st_size, 12)
            out = run_host("--load", str(save))
            self.assertIn("LOAD rc=0", out)
            out = run_host("--load", str(save), "--corrupt")
            self.assertIn("LOAD rc=-2", out)
            self.assertIn("CORRUPT", out)
            out = run_host("--nosave")
            self.assertIn("LOAD rc=-1", out)

    def test_quit_home_and_long_play(self):
        out = run_host("--home")
        self.assertIn("HOME want=1", out)
        out = run_host("--long")
        self.assertIn("LONG_PLAY", out)
        self.assertNotIn("LEAK", out)

    def test_contact_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            run_host("--demo", "--dump", str(d))
            names = ["title.ppm", "play.ppm", "chase.ppm", "map.ppm", "mission.ppm"]
            for n in names:
                self.assertTrue((d / n).is_file(), n)
            contact_sheet([d / n for n in names], CONTACT)
            self.assertTrue(CONTACT.is_file())
            self.assertGreater(CONTACT.stat().st_size, 1000)
            w, h, rgb = read_ppm(d / "title.ppm")
            self.assertEqual((w, h), (240, 432))
            title = ROOT / "docs" / "images" / "gta6-title.png"
            write_png(title, w, h, rgb)
            self.assertTrue(title.is_file())
            # title screen should not be the red low-mem fill
            px = rgb[100 * 3 * w + 120 * 3]
            self.assertNotEqual(rgb[0], 0x4c)

    def test_low_mem_screen(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            run_host("--tiny", "--dump", str(d))
            ppm = d / "lowmem.ppm"
            self.assertTrue(ppm.is_file())
            w, h, rgb = read_ppm(ppm)
            self.assertEqual((w, h), (240, 432))
            # dark red panel
            self.assertEqual(rgb[0], 0x4c)
            self.assertEqual(rgb[1], 0x0a)
            self.assertEqual(rgb[2], 0x0a)


if __name__ == "__main__":
    unittest.main()
