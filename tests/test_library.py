#!/usr/bin/env python3
"""Torn iTunes library DBs and restore-from-backup (Python FAT parser)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host.compat import Refused
from host.const import TESTED_DISK_SIZE
from host.device import Nano, annotate_size
from host.fat import MountFat, assert_safe_fat_path
from host.fatimg import make_nano7_fat_test_image, open_nano_fat_image
from host.library import (
    LIBRARY_FAT_PATHS,
    LIBRARY_OK,
    LIBRARY_TRUNCATED,
    classify_library_bytes,
    extract_library_from_backup,
    inspect_library_image,
    inspect_library_mount,
    is_library_restore_path,
    restore_library_from_backup,
)


def _sqlite_blob() -> bytes:
    td = tempfile.NamedTemporaryFile(suffix=".itdb", delete=False)
    td.close()
    con = sqlite3.connect(td.name)
    con.execute("CREATE TABLE item (id INTEGER)")
    con.execute("INSERT INTO item VALUES (1)")
    con.commit()
    con.close()
    data = Path(td.name).read_bytes()
    Path(td.name).unlink(missing_ok=True)
    return data


def _mhbd() -> bytes:
    return b"mhbd" + b"\x00" * 128


class ClassifyTests(unittest.TestCase):
    def test_torn_s_byte(self):
        row = classify_library_bytes("Library.itdb", b"S")
        self.assertTrue(row["truncated"])
        self.assertFalse(row["ok"])
        self.assertIn("1 byte", row["reason"])

    def test_zero_bytes(self):
        row = classify_library_bytes("iTunesCDB", b"")
        self.assertTrue(row["truncated"])

    def test_sqlite_ok(self):
        row = classify_library_bytes("Library.itdb", _sqlite_blob())
        self.assertTrue(row["ok"])
        self.assertEqual(row["kind"], "sqlite")

    def test_mhbd_ok(self):
        row = classify_library_bytes("iTunesCDB", _mhbd())
        self.assertTrue(row["ok"])
        self.assertEqual(row["kind"], "mhbd")


class PathAllowTests(unittest.TestCase):
    def test_prefixes_allowed_for_mkdir(self):
        self.assertTrue(is_library_restore_path(LIBRARY_FAT_PATHS[0]))
        self.assertTrue(is_library_restore_path("/iPod_Control/iTunes/iTunes Library.itlp"))
        self.assertTrue(is_library_restore_path("/iPod_Control/iTunes"))
        self.assertFalse(is_library_restore_path("/iPod_Control/Music/F00/a.mp3"))

    def test_assert_safe_still_refuses_without_flag(self):
        with self.assertRaises(Refused):
            assert_safe_fat_path("/iPod_Control/iTunes/iTunesCDB")
        assert_safe_fat_path("/iPod_Control/iTunes/iTunesCDB", allow_library=True)


class FatImageTests(unittest.TestCase):
    def test_lfn_roundtrip_library_paths(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "nano.img"
            make_nano7_fat_test_image(img)
            sql = _sqlite_blob()
            cdb = _mhbd()
            fat = open_nano_fat_image(img, writable=True)
            try:
                self.assertEqual(fat.offset, 262144)
                fat.write_file(LIBRARY_FAT_PATHS[0], sql)
                fat.write_file(LIBRARY_FAT_PATHS[1], sql)
                fat.write_file(LIBRARY_FAT_PATHS[2], cdb)
                self.assertEqual(fat.read_file(LIBRARY_FAT_PATHS[0]), sql)
                self.assertEqual(fat.read_file(LIBRARY_FAT_PATHS[2]), cdb)
            finally:
                fat.close()
            report = inspect_library_image(img)
            self.assertTrue(report["ok"])
            blobs = extract_library_from_backup(img)["files"]
            self.assertEqual(set(blobs), set(LIBRARY_FAT_PATHS))


class RestoreMountTests(unittest.TestCase):
    def test_read_library_without_write_flag(self):
        with tempfile.TemporaryDirectory() as td:
            vol = Path(td) / "vol"
            dest = vol / "iPod_Control" / "iTunes"
            dest.mkdir(parents=True)
            blob = _mhbd()
            (dest / "iTunesCDB").write_bytes(blob)
            ops = MountFat(vol)
            self.assertTrue(ops.exists("/iPod_Control/iTunes/iTunesCDB"))
            self.assertEqual(ops.read_bytes("/iPod_Control/iTunes/iTunesCDB"), blob)
            src = Path(td) / "cdb.bin"
            src.write_bytes(blob)
            with self.assertRaises(Refused):
                ops.copy_verified(src, "/iPod_Control/iTunes/iTunesCDB", replace=True)

    def test_mountfat_allow_library_copy(self):
        with tempfile.TemporaryDirectory() as td:
            vol = Path(td) / "vol"
            vol.mkdir()
            src = Path(td) / "lib.itdb"
            src.write_bytes(_sqlite_blob())
            ops = MountFat(vol, allow_library=True)
            ops.copy_verified(src, LIBRARY_FAT_PATHS[0], replace=True)
            dest = ops.host_path(LIBRARY_FAT_PATHS[0])
            self.assertTrue(dest.is_file())
            report = inspect_library_mount(vol)
            names = {f["name"] for f in report["files"]}
            self.assertIn("Library.itdb", names)

    def test_restore_from_backup_on_mounted_volume(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["NANO7_STATE_DIR"] = td
            img = Path(td) / "backup.img"
            make_nano7_fat_test_image(img)
            sql = _sqlite_blob()
            cdb = _mhbd()
            fat = open_nano_fat_image(img, writable=True)
            try:
                fat.write_file(LIBRARY_FAT_PATHS[0], sql)
                fat.write_file(LIBRARY_FAT_PATHS[1], sql)
                fat.write_file(LIBRARY_FAT_PATHS[2], cdb)
            finally:
                fat.close()
            vol = Path(td) / "vol"
            vol.mkdir()
            # Plant torn files on the live volume.
            torn = vol / "iPod_Control" / "iTunes" / "iTunes Library.itlp"
            torn.mkdir(parents=True)
            (torn / "Library.itdb").write_bytes(b"S")
            (torn / "Dynamic.itdb").write_bytes(b"")
            (vol / "iPod_Control" / "iTunes" / "iTunesCDB").write_bytes(b"")
            before = inspect_library_mount(vol)
            self.assertTrue(before["truncated"])

            import shutil
            from host.const import TESTED_DISK_SIZE
            from host.device import Nano, annotate_size

            src = ROOT / "untether" / "artifacts" / "device"
            shutil.copytree(src, vol / "Apps", dirs_exist_ok=True)
            nano = annotate_size(
                Nano(
                    os_name="linux",
                    device="/dev/loop9",
                    raw_device="/dev/loop9",
                    partition="/dev/loop9p1",
                    usb_serial="TESTSERIAL",
                    size=TESTED_DISK_SIZE,
                    fstype="vfat",
                    mountpoints=[str(vol)],
                    model="iPod",
                )
            )

            def fake_mount(_nano, **_kw):
                _nano.mountpoints = [str(vol)]
                return vol

            with mock.patch("host.library.is_macos", return_value=True), mock.patch(
                "host.library.find_nanos", return_value=[nano]
            ), mock.patch("host.library.ensure_fat_mount", side_effect=fake_mount), mock.patch(
                "host.library.assert_not_hfs"
            ), mock.patch(
                "host.untether.unmount"
            ), mock.patch(
                "host.untether.ensure_fat_mount", side_effect=fake_mount
            ), mock.patch(
                "host.untether.close_finder_ipod_windows", return_value=0
            ):
                rec = restore_library_from_backup(
                    nano,
                    backup=img,
                    backup_sha256="a" * 64,
                    skip_wait=True,
                    skip_backup_file=True,
                )
            self.assertTrue(rec["ok"])
            after = inspect_library_mount(vol)
            self.assertTrue(after["ok"])
            self.assertFalse(after["truncated"])
            self.assertEqual((torn / "Library.itdb").read_bytes(), sql)


if __name__ == "__main__":
    unittest.main()
