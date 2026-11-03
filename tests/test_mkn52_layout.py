#!/usr/bin/env python3
"""4096-byte-sector APM modelled on the MKN52ZP Finder Restore backup."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, quoted_path, sha256_file
from host.layout import (
    MKN52_APM_DISK_LBAS,
    MKN52_APM_HFS_SIZE,
    MKN52_APM_HFS_START,
    MKN52_APM_MAP_SIZE,
    MKN52_APM_MAP_START,
    MKN52_APM_SECTOR,
    MKN52_APM_TRAILING_SECTORS,
    MKN52_DISK_BYTES,
    MKN52_HFS_BYTES,
    SECTOR,
    build_apm_image,
    build_apm_macpod,
    inspect_image,
    pick_format_partition,
    write_mkn52_finder_image,
)
from host.winpod import build_winpod_file, convert_nano


class Mkn52FinderLayoutTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dir = Path(self.td.name)

    def test_constants_match_hardware_inspect(self):
        self.assertEqual(MKN52_HFS_BYTES, 3_856_960 * 4096)
        self.assertEqual(MKN52_HFS_BYTES, 15_798_108_160)
        self.assertEqual(MKN52_DISK_BYTES, 15_798_411_264)
        self.assertEqual(MKN52_DISK_BYTES, MKN52_APM_DISK_LBAS * MKN52_APM_SECTOR)
        self.assertEqual(MKN52_APM_TRAILING_SECTORS, 10)
        leftover = MKN52_DISK_BYTES - (
            MKN52_APM_HFS_START + MKN52_APM_HFS_SIZE
        ) * MKN52_APM_SECTOR
        self.assertEqual(leftover, 10 * 4096)

    def test_inspect_sparse_image_uses_4096_not_512(self):
        img = write_mkn52_finder_image(self.dir / "mkn52.img")
        self.assertEqual(img.stat().st_size, MKN52_DISK_BYTES)
        layout = inspect_image(img)
        self.assertEqual(layout.scheme, "apm")
        self.assertEqual(layout.sector_size, 4096)
        self.assertEqual(layout.size_bytes, MKN52_DISK_BYTES)
        self.assertEqual(len(layout.partitions), 2)
        p0, p1 = layout.partitions
        self.assertEqual(p0.type_name, "Apple_partition_map")
        self.assertEqual(p0.start_lba, MKN52_APM_MAP_START)
        self.assertEqual(p0.size_lba, MKN52_APM_MAP_SIZE)
        self.assertEqual(p0.size_bytes, MKN52_APM_MAP_SIZE * 4096)
        self.assertNotEqual(p0.size_bytes, MKN52_APM_MAP_SIZE * 512)
        self.assertEqual(p1.type_name, "Apple_HFS")
        self.assertEqual(p1.start_lba, MKN52_APM_HFS_START)
        self.assertEqual(p1.size_lba, MKN52_APM_HFS_SIZE)
        self.assertEqual(p1.size_bytes, MKN52_HFS_BYTES)
        self.assertNotEqual(p1.size_bytes, MKN52_APM_HFS_SIZE * 512)
        self.assertIsNone(layout.firmware)
        self.assertFalse(layout.is_macpod())
        self.assertTrue(layout.is_macpod_usb_data())
        self.assertFalse(layout.is_winpod())
        summary = layout.summary()
        self.assertIn("Macpod data volume", summary)
        self.assertIn(str(MKN52_HFS_BYTES), summary)
        self.assertNotIn("unknown (fail closed", summary)
        self.assertIn("NAND", " ".join(layout.notes))
        self.assertTrue(any("10 sectors" in n for n in layout.notes))
        target = pick_format_partition(layout)
        self.assertEqual(target.type_name, "Apple_HFS")
        self.assertEqual(target.index, 1)

    def test_prefix_only_still_has_correct_hfs_bytes(self):
        img = write_mkn52_finder_image(
            self.dir / "prefix.img", sparse_full_size=False
        )
        layout = inspect_image(img)
        self.assertEqual(layout.sector_size, 4096)
        self.assertEqual(layout.data.size_bytes, MKN52_HFS_BYTES)
        self.assertLess(layout.size_bytes, MKN52_DISK_BYTES)

    def test_build_image_refuses_finder_layout(self):
        src = write_mkn52_finder_image(
            self.dir / "src.img", sparse_full_size=False
        )
        with self.assertRaises(Refused) as ctx:
            build_winpod_file(src, self.dir / "out.img")
        msg = str(ctx.exception)
        self.assertIn("no Apple_MDFW", msg)
        self.assertIn("IPodRepartition", msg)
        self.assertFalse((self.dir / "out.img").exists())

    def test_4k_macpod_with_mdfw_scales_mbr_lbas(self):
        import shutil

        if not shutil.which("mkfs.vfat") and not shutil.which("mkfs.msdos") and not shutil.which(
            "mformat"
        ):
            self.skipTest("no FAT formatter")
        # 4K APM: map + MDFW + ~1.2 MiB HFS so format_fat32_region will run.
        sector = 4096
        fw_start, fw_size = 8, 8
        data_start = 16
        data_size = 320  # 320 * 4096 = 1,310,720
        disk_lbas = data_start + data_size
        src = self.dir / "macpod4k.img"
        src.write_bytes(
            build_apm_image(
                sector,
                disk_lbas,
                [
                    (1, 3, "Apple", "Apple_partition_map"),
                    (fw_start, fw_size, "firmware", "Apple_MDFW"),
                    (data_start, data_size, "iPod", "Apple_HFS"),
                ],
                firmware_marker=b"FWREGION",
            )
        )
        layout = inspect_image(src)
        self.assertTrue(layout.is_macpod())
        self.assertEqual(layout.sector_size, 4096)
        self.assertEqual(layout.firmware.start_bytes, fw_start * 4096)
        dest = self.dir / "winpod.img"
        result = build_winpod_file(src, dest)
        self.assertTrue(result.is_winpod())
        self.assertEqual(result.sector_size, SECTOR)
        scale = 4096 // 512
        self.assertEqual(result.firmware.start_lba, fw_start * scale)
        self.assertEqual(result.firmware.size_lba, fw_size * scale)
        self.assertEqual(result.data.start_lba, data_start * scale)
        self.assertEqual(result.firmware.start_bytes, fw_start * 4096)
        with open(dest, "rb") as f:
            f.seek(fw_start * 4096)
            self.assertEqual(f.read(8), b"FWREGION")

    def test_512_macpod_still_inspects(self):
        img = self.dir / "classic.img"
        img.write_bytes(
            build_apm_macpod(
                8192,
                fw_start=8,
                fw_size=100,
                data_start=108,
                data_size=8000,
                firmware_marker=b"FWREGION",
            )
        )
        layout = inspect_image(img)
        self.assertTrue(layout.is_macpod())
        self.assertEqual(layout.sector_size, 512)
        self.assertEqual(layout.firmware.start_bytes, 8 * 512)

    def test_convert_dry_run_accepts_finder_layout(self):
        backup = write_mkn52_finder_image(
            self.dir / "nano-Apple iPod Media-20261028T171800Z.img",
            sparse_full_size=False,
        )
        size = backup.stat().st_size
        bhash = sha256_file(backup)
        os.environ["NANO7_STATE_DIR"] = str(self.dir)
        os.environ["NANO7KIT_CONFIG_DIR"] = str(self.dir / "cfg")
        from host.device import Nano, annotate_size

        nano = annotate_size(
            Nano(
                os_name="darwin",
                device="/dev/disk12",
                raw_device="/dev/rdisk12",
                partition="/dev/rdisk12s1",
                usb_serial="CCQKMOCKSERIAL",
                size=size,
                fstype="hfs",
                mountpoints=[],
                model="iPod",
            )
        )
        mse = self.dir / "stock.mse"
        mse.write_bytes(b"stock-stub")
        wind3x = self.dir / "fake-wind3x"
        wind3x.write_text("#!/bin/sh\nexit 0\n")
        wind3x.chmod(wind3x.stat().st_mode | stat.S_IXUSR)
        called = {"full": False}

        def boom(*a, **k):
            called["full"] = True
            raise AssertionError("restore --full must not run on dry-run")

        with mock.patch("host.safety.check_stock_mse", return_value="c" * 64), mock.patch(
            "host.winpod.select_wind3x", return_value=(wind3x, False, "fake")
        ), mock.patch("host.winpod.wait_dfu"), mock.patch(
            "host.winpod.prepare_restore",
            return_value=(
                'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
                "SerialNumber: ABOUTSERIAL\n"
                "Recovery mode ready; no device partitions were written\n"
            ),
        ), mock.patch("host.winpod.restore_full", side_effect=boom), mock.patch(
            "host.usb.late_wtf_present", return_value=False
        ):
            out = convert_nano(
                nano,
                backup=backup,
                backup_sha256=bhash,
                serial="ABOUTSERIAL",
                dry_run=True,
                allow_size=size,
                confirm_untested=True,
                accept_untested_flasher=True,
                accept_2015_refresh=True,
                mse=mse,
            )
        self.assertTrue(out["dry_run"])
        self.assertFalse(called["full"])

    def test_quoted_path_survives_spaces(self):
        p = Path("/Users/you/nano7-backups/nano-Apple iPod Media-20261028T171800Z.img")
        q = quoted_path(p)
        self.assertIn(" ", str(p))
        self.assertTrue(q.startswith("'") or "\\" in q)
        self.assertIn("Apple", q)


if __name__ == "__main__":
    unittest.main()
