#!/usr/bin/env python3
"""Disk-image tests for Winpod/Macpod inspect, FAT32 format, and conversion refusals."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.const import FIRMWARE_MSE_SHA256, RESCUE_MSE_SHA256, FIRMWARE_MSE_SIZE
from host.layout import (
    SECTOR,
    build_apm_macpod,
    build_mbr_winpod,
    encode_mbr,
    inspect_image,
    partition_node,
    pick_format_partition,
)
from host.safety import check_stock_mse
from host.winpod import build_winpod_file, convert_nano, format_fat32_region


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


class LayoutInspectTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dir = Path(self.td.name)

    def test_mbr_winpod(self):
        img = _write(
            self.dir / "winpod.img",
            build_mbr_winpod(8192, fw_start=63, fw_size=200, firmware_marker=b"FWREGION"),
        )
        layout = inspect_image(img)
        self.assertEqual(layout.scheme, "mbr")
        self.assertTrue(layout.is_winpod())
        self.assertFalse(layout.is_macpod())
        self.assertEqual(layout.firmware.start_lba, 63)
        self.assertEqual(layout.firmware.type_id, 0x00)
        self.assertEqual(layout.data.type_id, 0x0B)
        target = pick_format_partition(layout)
        self.assertEqual(target.index, 1)
        with open(img, "rb") as f:
            f.seek(63 * SECTOR)
            self.assertEqual(f.read(8), b"FWREGION")

    def test_apm_macpod(self):
        img = _write(
            self.dir / "macpod.img",
            build_apm_macpod(
                8192, fw_start=8, fw_size=100, data_start=108, data_size=8000, firmware_marker=b"FWREGION"
            ),
        )
        layout = inspect_image(img)
        self.assertEqual(layout.scheme, "apm")
        self.assertTrue(layout.is_macpod())
        self.assertFalse(layout.is_winpod())
        self.assertEqual(layout.firmware.type_name, "Apple_MDFW")
        self.assertEqual(layout.data.type_name, "Apple_HFS")
        target = pick_format_partition(layout)
        self.assertEqual(target.type_name, "Apple_HFS")

    def test_gpt_refused_for_format(self):
        raw = bytearray(4096)
        raw[512:520] = b"EFI PART"
        raw[510] = 0x55
        raw[511] = 0xAA
        img = _write(self.dir / "gpt.img", bytes(raw))
        layout = inspect_image(img)
        self.assertEqual(layout.scheme, "gpt")
        with self.assertRaises(Refused) as ctx:
            pick_format_partition(layout)
        self.assertIn("GPT", str(ctx.exception))

    def test_unknown_refused_for_format(self):
        img = _write(self.dir / "blank.img", b"\x00" * 4096)
        layout = inspect_image(img)
        self.assertEqual(layout.scheme, "unknown")
        with self.assertRaises(Refused) as ctx:
            pick_format_partition(layout)
        self.assertIn("guess a FAT32 slice", str(ctx.exception))

    def test_does_not_format_type0_firmware_slot(self):
        img = bytearray(build_mbr_winpod(4096, fw_start=63, fw_size=100))
        # Second slot also type 0x00
        img[0:512] = encode_mbr([(0x00, 63, 100), (0x00, 163, 100)], 4096)
        path = _write(self.dir / "both0.img", bytes(img))
        layout = inspect_image(path)
        with self.assertRaises(Refused) as ctx:
            pick_format_partition(layout)
        self.assertIn("0x00", str(ctx.exception))

    def test_partition_node_names(self):
        self.assertEqual(partition_node("/dev/rdisk9", 1), "/dev/rdisk9s2")
        self.assertEqual(partition_node("/dev/disk3", 0), "/dev/disk3s1")
        self.assertEqual(partition_node("/dev/sda", 1), "/dev/sda2")
        self.assertEqual(partition_node("/dev/mmcblk0", 1), "/dev/mmcblk0p2")
        self.assertEqual(partition_node("/dev/loop0", 0), "/dev/loop0p1")


class BuildWinpodFileTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dir = Path(self.td.name)

    def test_apm_to_winpod_preserves_firmware_and_makes_fat(self):
        import shutil

        if not shutil.which("mkfs.vfat") and not shutil.which("mkfs.msdos") and not shutil.which("mformat"):
            self.skipTest("no FAT formatter")
        src = _write(
            self.dir / "macpod.img",
            build_apm_macpod(
                16384,
                fw_start=8,
                fw_size=64,
                data_start=72,
                data_size=16384 - 72,
                firmware_marker=b"FWREGION",
            ),
        )
        dest = self.dir / "winpod.img"
        layout = build_winpod_file(src, dest)
        self.assertTrue(layout.is_winpod())
        self.assertEqual(layout.firmware.type_id, 0x00)
        self.assertEqual(layout.data.type_id, 0x0B)
        with open(dest, "rb") as f:
            f.seek(8 * SECTOR)
            self.assertEqual(f.read(8), b"FWREGION")
        from host.winpod import _fat_readable

        self.assertTrue(_fat_readable(dest, layout.data.start_bytes))

    def test_refuses_live_device_destination(self):
        src = _write(
            self.dir / "macpod.img",
            build_apm_macpod(4096, 8, 16, 24, 4000, firmware_marker=b"FWREGION"),
        )
        with self.assertRaises(Refused) as ctx:
            build_winpod_file(src, Path("/dev/null"))
        self.assertIn("live device", str(ctx.exception).lower())

    def test_refuses_non_macpod_source(self):
        src = _write(
            self.dir / "winpod.img",
            build_mbr_winpod(4096, fw_start=63, fw_size=50),
        )
        with self.assertRaises(Refused) as ctx:
            build_winpod_file(src, self.dir / "out.img")
        self.assertIn("Macpod", str(ctx.exception))

    def test_refuses_overwrite(self):
        src = _write(
            self.dir / "macpod.img",
            build_apm_macpod(4096, 8, 16, 24, 4000, firmware_marker=b"FWREGION"),
        )
        dest = self.dir / "out.img"
        dest.write_bytes(b"nope")
        with self.assertRaises(Refused):
            build_winpod_file(src, dest)


class StockMseTests(unittest.TestCase):
    def test_refuses_patched_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Firmware.MSE"
            p.write_bytes(b"x" * 64)
            with self.assertRaises(Refused) as ctx:
                check_stock_mse(p)
            self.assertIn("size", str(ctx.exception))
            # Size matches, hash is the untether image.
            with mock.patch("host.safety.file_size", return_value=FIRMWARE_MSE_SIZE), mock.patch(
                "host.safety.sha256_file", return_value=FIRMWARE_MSE_SHA256
            ):
                with self.assertRaises(Refused) as ctx:
                    check_stock_mse(p)
                self.assertIn("patched untether", str(ctx.exception))
            with mock.patch("host.safety.file_size", return_value=FIRMWARE_MSE_SIZE), mock.patch(
                "host.safety.sha256_file", return_value=RESCUE_MSE_SHA256
            ):
                with self.assertRaises(Refused) as ctx:
                    check_stock_mse(p)
                self.assertIn("rescue", str(ctx.exception))


class ConvertMockTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        self.size = 8192
        self.backup = Path(self.td.name) / "nano.img"
        self.backup.write_bytes(b"\x00" * self.size)
        self.bhash = sha256_file(self.backup)
        self.mse = Path(self.td.name) / "stock.mse"
        self.mse.write_bytes(b"stock-stub")
        self.wind3x = Path(self.td.name) / "fake-wind3x"
        self.wind3x.write_text("#!/bin/sh\nexit 0\n")
        self.wind3x.chmod(self.wind3x.stat().st_mode | stat.S_IXUSR)

    def _nano(self):
        from host.device import Nano, annotate_size

        return annotate_size(
            Nano(
                os_name="linux",
                device="/dev/loop9",
                raw_device="/dev/loop9",
                partition="/dev/loop9p2",
                usb_serial="USB1",
                size=self.size,
                fstype="hfs",
                mountpoints=[],
                model="iPod",
            )
        )

    def test_missing_backup_file_refused(self):
        missing = Path(self.td.name) / "no-such.img"
        with self.assertRaises(Refused) as ctx:
            convert_nano(
                self._nano(),
                backup=missing,
                backup_sha256=self.bhash,
                serial="SN1",
                allow_size=self.size,
                confirm_untested=True,
            )
        self.assertIn("backup missing", str(ctx.exception).lower())

    def test_missing_serial_refused(self):
        with self.assertRaises(Refused) as ctx:
            convert_nano(
                self._nano(),
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="",
                allow_size=self.size,
                confirm_untested=True,
            )
        self.assertIn("serial", str(ctx.exception).lower())

    def test_already_winpod_backup_refused(self):
        self.backup.write_bytes(build_mbr_winpod(self.size // SECTOR, fw_start=1, fw_size=2))
        self.bhash = sha256_file(self.backup)
        with self.assertRaises(Refused) as ctx:
            convert_nano(
                self._nano(),
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                allow_size=self.size,
                confirm_untested=True,
                mse=self.mse,
            )
        self.assertIn("already a Winpod", str(ctx.exception))

    def test_dry_run_does_not_call_restore_full(self):
        from tests.test_device_flows import _fake_wind3x

        wind3x = _fake_wind3x(Path(self.td.name), serial="SN1")
        called = {"full": False}
        def boom(*a, **k):
            called["full"] = True
            raise AssertionError("restore --full must not run on dry-run")

        with mock.patch("host.safety.check_stock_mse", return_value="c" * 64), mock.patch(
            "host.winpod.select_wind3x", return_value=(wind3x, False, "fake")
        ), mock.patch("host.winpod.wait_dfu"), mock.patch(
            "host.winpod.prepare_restore",
            return_value=(
                'Found device device="Nano 7G" interface=diskmode\n'
                "SerialNumber: SN1\n"
                "Recovery mode ready; no device partitions were written\n"
            ),
        ), mock.patch("host.winpod.restore_full", side_effect=boom), mock.patch(
            "host.usb.late_wtf_present", return_value=False
        ):
            out = convert_nano(
                self._nano(),
                backup=self.backup,
                backup_sha256=self.bhash,
                serial="SN1",
                dry_run=True,
                allow_size=self.size,
                confirm_untested=True,
                accept_untested_flasher=True,
                mse=self.mse,
            )
        self.assertTrue(out["dry_run"])
        self.assertFalse(called["full"])

    def test_2015_usb_refused_without_flag(self):
        with mock.patch("host.safety.check_stock_mse", return_value="c" * 64), mock.patch(
            "host.winpod.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch("host.usb.late_wtf_present", return_value=True):
            with self.assertRaises(Refused) as ctx:
                convert_nano(
                    self._nano(),
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    serial="SN1",
                    dry_run=True,
                    allow_size=self.size,
                    confirm_untested=True,
                    accept_untested_flasher=True,
                    mse=self.mse,
                )
        self.assertIn("2015", str(ctx.exception).lower())


class Wind3xPatchTests(unittest.TestCase):
    def test_patch_promotes_nano7late_and_keeps_no_dfu_pid(self):
        patch = Path(__file__).resolve().parent.parent / "untether" / "patches" / "wind3x-nano7.patch"
        text = patch.read_text()
        self.assertIn("switchedLate", text)
        self.assertIn("0x124a0000 / 39A10023", text)
        self.assertIn("Disk: 0x1267", text)
        self.assertIn("Nano7Late", text)
        late_at = text.rfind("Kind:            Nano7Late")
        self.assertGreater(late_at, 0)
        late_hunk = text[late_at - 500 : late_at + 80]
        self.assertNotIn("DFU:", late_hunk)
        self.assertIn("WTF:", late_hunk)
        self.assertIn("Disk:", late_hunk)
        self.assertIn("UpdaterFamilyID: 39", late_hunk)
        self.assertIn("--bootloader-only", text)
        self.assertIn("RefuseNano7Late37A40005", text)
        self.assertIn("7d921c6d97305e3ef944c1f6212cab2adcbb4a5d4c2ff45faeafc438aa3b1548", text)


class FormatRegionTests(unittest.TestCase):
    def test_format_fat32_region_readable(self):
        import shutil

        if not shutil.which("mkfs.vfat") and not shutil.which("mkfs.msdos") and not shutil.which("mformat"):
            self.skipTest("no FAT formatter")
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "part.img"
            img.write_bytes(b"\x00" * (4 * 1024 * 1024))
            format_fat32_region(img, 0, 4 * 1024 * 1024)
            from host.winpod import _fat_readable

            self.assertTrue(_fat_readable(img, 0))


if __name__ == "__main__":
    unittest.main()
