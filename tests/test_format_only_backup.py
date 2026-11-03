#!/usr/bin/env python3
"""--format-only pre-repartition backup vs IPodRepartition live size."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused
from host.layout import (
    MKN52_DISK_BYTES,
    MKN52_IPODREPARTITION_DELTA,
    MKN52_RECOVERY_BYTES,
    encode_mkn52_repartition_mbr,
    inspect_bytes,
    inspect_image,
    write_mkn52_finder_image,
)
from host.safety import backup_verify_size, check_backup, ipodrepartition_delta_ok
from host.device import Nano, usable_nano
from host.winpod import check_format_only_backup_vs_live, convert_nano


class BackupVerifySizeTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.path = Path(self.td.name) / "nano.img"
        self.path.write_bytes(b"abc")

    def test_format_only_hashes_file_not_live_scsi(self):
        self.assertEqual(
            backup_verify_size(
                self.path,
                nano_size=MKN52_RECOVERY_BYTES,
                receipt_size=MKN52_DISK_BYTES,
                format_only=True,
            ),
            3,
        )

    def test_scsi_dump_hashes_file_not_live_scsi(self):
        self.assertEqual(
            backup_verify_size(
                self.path,
                nano_size=MKN52_RECOVERY_BYTES,
                format_only=False,
                scsi_dump=True,
            ),
            3,
        )

    def test_full_convert_hashes_live_nano(self):
        self.assertEqual(
            backup_verify_size(
                self.path,
                nano_size=MKN52_RECOVERY_BYTES,
                receipt_size=MKN52_DISK_BYTES,
                format_only=False,
            ),
            MKN52_RECOVERY_BYTES,
        )

    def test_format_only_ignores_size_zero_live(self):
        self.assertEqual(
            backup_verify_size(
                self.path,
                nano_size=0,
                receipt_size=MKN52_DISK_BYTES,
                format_only=True,
            ),
            3,
        )

    def test_format_only_without_file_uses_receipt(self):
        missing = Path(self.td.name) / "missing.img"
        self.assertEqual(
            backup_verify_size(
                missing,
                nano_size=MKN52_RECOVERY_BYTES,
                receipt_size=MKN52_DISK_BYTES,
                format_only=True,
            ),
            MKN52_DISK_BYTES,
        )


class UsableNanoTests(unittest.TestCase):
    def test_size_zero_is_dropped(self):
        ghost = Nano(
            os_name="darwin",
            device="/dev/disk9",
            raw_device="/dev/rdisk9",
            partition="",
            usb_serial="USB1",
            size=0,
            fstype="",
            mountpoints=[],
            model="iPod",
        )
        self.assertIsNone(usable_nano(ghost))
        self.assertIsNone(usable_nano(None))
        ghost.size = MKN52_DISK_BYTES
        self.assertIs(usable_nano(ghost), ghost)


class IpodrepartitionDeltaTests(unittest.TestCase):
    def test_mkn52_delta_ok(self):
        self.assertTrue(
            ipodrepartition_delta_ok(MKN52_RECOVERY_BYTES, MKN52_DISK_BYTES)
        )
        self.assertEqual(MKN52_IPODREPARTITION_DELTA, 8_212_480)
        self.assertFalse(ipodrepartition_delta_ok(MKN52_DISK_BYTES, MKN52_DISK_BYTES))
        self.assertFalse(ipodrepartition_delta_ok(MKN52_DISK_BYTES, MKN52_RECOVERY_BYTES))
        self.assertFalse(
            ipodrepartition_delta_ok(MKN52_DISK_BYTES + 17 * 1024 * 1024, MKN52_DISK_BYTES)
        )
        self.assertFalse(ipodrepartition_delta_ok(MKN52_DISK_BYTES + 100, MKN52_DISK_BYTES))


class FormatOnlyBackupGateTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        self.backup = write_mkn52_finder_image(Path(self.td.name) / "finder.img")
        self.backup_layout = inspect_image(self.backup)
        self.live_layout = inspect_bytes(encode_mkn52_repartition_mbr(), MKN52_RECOVERY_BYTES)
        self.assertTrue(self.backup_layout.is_macpod_usb_data())
        self.assertIsNone(self.backup_layout.firmware)
        self.assertTrue(self.live_layout.is_ipodrepartition_4k_fat32())

    def _started(self, serial="XYZ123ABC456"):
        from host.state import write_receipt

        write_receipt("FAT32_STARTED", {"serial": serial, "utc": "t"})

    def test_accepts_mkn52_pre_repartition_backup(self):
        self._started()
        out = check_format_only_backup_vs_live(
            serial="XYZ123ABC456",
            backup=self.backup,
            backup_layout=self.backup_layout,
            backup_size=MKN52_DISK_BYTES,
            live_layout=self.live_layout,
            live_size=MKN52_RECOVERY_BYTES,
        )
        self.assertTrue(out["accepted"])
        self.assertEqual(out["delta"], MKN52_IPODREPARTITION_DELTA)

    def test_equal_sizes_match_without_receipt(self):
        out = check_format_only_backup_vs_live(
            serial="XYZ123ABC456",
            backup=self.backup,
            backup_layout=self.backup_layout,
            backup_size=MKN52_RECOVERY_BYTES,
            live_layout=self.live_layout,
            live_size=MKN52_RECOVERY_BYTES,
        )
        self.assertTrue(out["match"])
        self.assertFalse(out["accepted"])

    def test_refuses_without_fat32_started(self):
        with self.assertRaises(Refused) as ctx:
            check_format_only_backup_vs_live(
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_layout=self.backup_layout,
                backup_size=MKN52_DISK_BYTES,
                live_layout=self.live_layout,
                live_size=MKN52_RECOVERY_BYTES,
            )
        msg = str(ctx.exception)
        self.assertIn("backup size", msg)
        self.assertIn("FAT32_STARTED", msg)
        self.assertIn("Do not pass --replace", msg)

    def test_refuses_wrong_serial(self):
        self._started("OTHER")
        with self.assertRaises(Refused) as ctx:
            check_format_only_backup_vs_live(
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_layout=self.backup_layout,
                backup_size=MKN52_DISK_BYTES,
                live_layout=self.live_layout,
                live_size=MKN52_RECOVERY_BYTES,
            )
        self.assertIn("serial mismatch", str(ctx.exception))

    def test_refuses_live_not_4k_0x0c(self):
        self._started()
        from host.layout import build_mbr_winpod

        blob = build_mbr_winpod(16384, fw_start=63, fw_size=200)
        live = inspect_bytes(blob[:1024], len(blob))
        with self.assertRaises(Refused) as ctx:
            check_format_only_backup_vs_live(
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_layout=self.backup_layout,
                backup_size=MKN52_DISK_BYTES,
                live_layout=live,
                live_size=MKN52_RECOVERY_BYTES,
            )
        self.assertIn("0x0c", str(ctx.exception))

    def test_refuses_winpod_backup(self):
        self._started()
        from host.layout import build_mbr_winpod

        blob = build_mbr_winpod(16384, fw_start=63, fw_size=200)
        backup_layout = inspect_bytes(blob[:1024], len(blob))
        with self.assertRaises(Refused) as ctx:
            check_format_only_backup_vs_live(
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_layout=backup_layout,
                backup_size=MKN52_DISK_BYTES,
                live_layout=self.live_layout,
                live_size=MKN52_RECOVERY_BYTES,
            )
        self.assertIn("APM/HFS", str(ctx.exception))

    def test_refuses_live_size_zero(self):
        self._started()
        with self.assertRaises(Refused) as ctx:
            check_format_only_backup_vs_live(
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_layout=self.backup_layout,
                backup_size=MKN52_DISK_BYTES,
                live_layout=self.live_layout,
                live_size=0,
            )
        msg = str(ctx.exception)
        self.assertIn("never 0 bytes", msg)
        self.assertNotIn("device not in recovery diskmode", msg)

    def test_refuses_delta_too_large(self):
        self._started()
        with self.assertRaises(Refused) as ctx:
            check_format_only_backup_vs_live(
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_layout=self.backup_layout,
                backup_size=MKN52_DISK_BYTES,
                live_layout=self.live_layout,
                live_size=MKN52_DISK_BYTES + 32 * 1024 * 1024,
            )
        self.assertIn("delta", str(ctx.exception))


class FormatOnlyConvertNanoTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        self.backup = write_mkn52_finder_image(Path(self.td.name) / "finder.img")
        self.hash = "cd" * 32
        self.wind3x = Path(self.td.name) / "fake-wind3x"
        self.wind3x.write_text("#!/bin/sh\nexit 0\n")
        self.wind3x.chmod(self.wind3x.stat().st_mode | stat.S_IXUSR)

    def test_full_convert_still_refuses_recovery_size(self):
        from host.device import Nano, annotate_size

        nano = annotate_size(
            Nano(
                os_name="darwin",
                device="/dev/disk12",
                raw_device="/dev/rdisk12",
                partition="/dev/rdisk12s1",
                usb_serial="USB1",
                size=MKN52_RECOVERY_BYTES,
                fstype="hfs",
                mountpoints=[],
                model="iPod",
            )
        )
        with mock.patch("host.safety.sha256_file", return_value=self.hash):
            with self.assertRaises(Refused) as ctx:
                convert_nano(
                    nano,
                    backup=self.backup,
                    backup_sha256=self.hash,
                    serial="XYZ123ABC456",
                    dry_run=True,
                )
        msg = str(ctx.exception)
        self.assertTrue("allow-disk-size" in msg or "backup size" in msg, msg)

    def test_scsi_dump_accepts_finder_backup_against_recovery_nano(self):
        from host.device import Nano, annotate_size

        nano = annotate_size(
            Nano(
                os_name="darwin",
                device="/dev/disk12",
                raw_device="/dev/rdisk12",
                partition="/dev/rdisk12s1",
                usb_serial="USB1",
                size=MKN52_RECOVERY_BYTES,
                fstype="hfs",
                mountpoints=[],
                model="iPod",
            )
        )
        dest = Path(self.td.name) / "scsi-dump"
        dumped = {"n": 0}

        def dump_ok(d, **k):
            dumped["n"] += 1
            Path(d).mkdir(parents=True, exist_ok=True)
            return {"method": "scsi-dump", "wrote": False, "dest": str(d)}

        with mock.patch("host.safety.sha256_file", return_value=self.hash), mock.patch(
            "host.winpod.diskmode_usb_present", return_value=True
        ), mock.patch("host.usbms.dump_data_slice_via_bot", side_effect=dump_ok):
            out = convert_nano(
                nano,
                backup=self.backup,
                backup_sha256=self.hash,
                serial="XYZ123ABC456",
                scsi_dump=dest,
            )
        self.assertEqual(out.get("method"), "scsi-dump")
        self.assertEqual(dumped["n"], 1)

    def test_check_backup_still_strict_on_size(self):
        with self.assertRaises(Refused) as ctx:
            check_backup(self.backup, self.hash, MKN52_RECOVERY_BYTES)
        self.assertIn("backup size", str(ctx.exception))
        self.assertIn(str(MKN52_DISK_BYTES), str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
