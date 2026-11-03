#!/usr/bin/env python3
"""Disk Mode Game Boy ROM copy into /Apps/Data/GameBoy."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.compat import Refused, sha256_file
from host.const import TESTED_DISK_SIZE
from host.device import Nano, annotate_size
from host.roms import GAMEBOY_FAT_DIR, ROMS_OK, add_roms, rom_basename


def _nano(**kw) -> Nano:
    defaults = dict(
        os_name="linux",
        device="/dev/loop9",
        raw_device="/dev/loop9",
        partition="/dev/loop9p1",
        usb_serial="TESTSERIAL",
        size=TESTED_DISK_SIZE,
        fstype="vfat",
        mountpoints=[],
        model="iPod",
    )
    defaults.update(kw)
    return annotate_size(Nano(**defaults))


def _plant_tested_apps(vol: Path) -> None:
    src = ROOT / "untether" / "artifacts" / "device"
    dest = vol / "Apps"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


class RomNameTests(unittest.TestCase):
    def test_accepts_gb_gbc(self):
        self.assertEqual(rom_basename(Path("Tetris.gb")), "Tetris.gb")
        self.assertEqual(rom_basename(Path("Zelda.GBC")), "Zelda.GBC")

    def test_refuses_other_ext_and_path(self):
        with self.assertRaises(Refused) as ctx:
            rom_basename(Path("mario.z64"))
        self.assertIn(".gb", str(ctx.exception))
        with self.assertRaises(Refused):
            rom_basename(Path(".hidden.gb"))


class RomCopyTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name

    def test_add_copies_and_keeps_loader_hashes(self):
        vol = Path(self.td.name) / "vol"
        _plant_tested_apps(vol)
        gb = Path(self.td.name) / "tiny.gb"
        gb.write_bytes(b"FAKEGB")
        nano = _nano(mountpoints=[str(vol)])

        def fake_mount(_nano, **_kw):
            _nano.mountpoints = [str(vol)]
            return vol

        with mock.patch("host.roms.find_nanos", return_value=[nano]), mock.patch(
            "host.roms.ensure_fat_mount", side_effect=fake_mount
        ), mock.patch("host.roms.assert_not_hfs"), mock.patch(
            "host.untether.unmount"
        ), mock.patch(
            "host.untether.ensure_fat_mount", side_effect=fake_mount
        ), mock.patch("host.untether.sync_mount"), mock.patch(
            "host.untether.close_finder_ipod_windows", return_value=0
        ):
            out = add_roms(nano, [gb], skip_wait=True)
        dest = vol / "Apps" / "Data" / "GameBoy" / "tiny.gb"
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_bytes(), b"FAKEGB")
        self.assertEqual(out["dir"], GAMEBOY_FAT_DIR)
        self.assertTrue(out["loader_ok"])
        self.assertEqual(out["hashes"]["/Apps/Data/GameBoy/tiny.gb"], sha256_file(gb))
        self.assertTrue((vol / "Apps" / "Startup" / "resident.hbapp").is_file())

    def test_refuses_non_rom(self):
        vol = Path(self.td.name) / "vol"
        vol.mkdir()
        other = Path(self.td.name) / "notes.txt"
        other.write_text("no")
        nano = _nano(mountpoints=[str(vol)])
        with self.assertRaises(Refused) as ctx:
            add_roms(nano, [other], skip_wait=True)
        self.assertIn(".gb", str(ctx.exception))

    def test_cli_help(self):
        import subprocess

        r = subprocess.run(
            [sys.executable, str(ROOT / "start"), "roms", "add", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("GameBoy", r.stdout)
        self.assertNotIn("\u2014", r.stdout)
        src = (ROOT / "host" / "roms.py").read_text(encoding="utf-8")
        self.assertIn("durable_flush_and_rehash", src)
        self.assertIn(ROMS_OK, src)
        self.assertIn("Disk Mode", src)


if __name__ == "__main__":
    unittest.main()
