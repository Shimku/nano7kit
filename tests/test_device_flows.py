#!/usr/bin/env python3
"""Device-flow mocks: FAT image, refusals, fake wInd3x, fake diskutil, fake SSH."""
from __future__ import annotations

import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from host.compat import Refused, sha256_file
from host.const import (
    AB_SHA256,
    FIRMWARE_MSE_SHA256,
    RESIDENT_SHA256,
    SLOT_A_SHA256,
    TESTED_DISK_SIZE,
)
from host.device import Nano, annotate_size, find_nanos
from host.fat import assert_not_hfs, assert_safe_fat_path, copy_verified, exists, mkdir_p
from host.safety import check_ab_receipt, check_backup, check_disk_size, check_usb_serial
from host.state import write_receipt
from host.untether import flash_firmware, install_loader_files
from host.usb import check_prepare_log, parse_ioreg_usb, prepare_restore


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


def _ab_receipt(state: Path, usb_serial="TESTSERIAL") -> None:
    os.environ["NANO7_STATE_DIR"] = str(state)
    write_receipt(
        "AB_LOADER_FILES_VERIFIED",
        {
            "verified": "yes",
            "usb_serial": usb_serial,
            "active_slot": "A",
            "ab_sha256": AB_SHA256,
            "slot_a_sha256": SLOT_A_SHA256,
            "resident_sha256": RESIDENT_SHA256,
        },
    )


def _fake_wind3x(td: Path, serial="ABC123DEF") -> Path:
    path = td / "fake-wind3x"
    path.write_text(
        "#!/bin/sh\n"
        "if printf '%s' \"$*\" | grep -q -- --prepare-only; then\n"
        '  echo \'Found device device="Nano 7G" interface=diskmode\'\n'
        f"  echo 'SerialNumber: {serial}'\n"
        "  echo 'Recovery mode ready; no device partitions were written'\n"
        "  exit 0\n"
        "fi\n"
        "echo 'Writing firmware...'\n"
        "echo 'Resetting...'\n"
        "exit 0\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class FatImageTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("mkfs.vfat") or not shutil.which("mcopy"):
            self.skipTest("mtools/mkfs.vfat required")
        self.td = tempfile.TemporaryDirectory()
        self.img = Path(self.td.name) / "fat.img"
        subprocess.check_call(
            ["dd", "if=/dev/zero", f"of={self.img}", "bs=1M", "count=8", "status=none"]
        )
        subprocess.check_call(["mkfs.vfat", "-F", "32", "-n", "IPOD", str(self.img)], stdout=subprocess.DEVNULL)

    def tearDown(self):
        self.td.cleanup()

    def test_copy_verified_roundtrip(self):
        src = Path(self.td.name) / "resident.hbapp"
        src.write_bytes(b"hello-nano")
        mkdir_p(str(self.img), "/Apps/Startup")
        status = copy_verified(str(self.img), src, "/Apps/Startup/resident.hbapp")
        self.assertEqual(status, "wrote")
        self.assertEqual(copy_verified(str(self.img), src, "/Apps/Startup/resident.hbapp"), "already")
        src.write_bytes(b"changed")
        with self.assertRaises(Refused) as ctx:
            copy_verified(str(self.img), src, "/Apps/Startup/resident.hbapp")
        self.assertIn("different hash", str(ctx.exception))

    def test_itunes_db_refused(self):
        with self.assertRaises(Refused):
            assert_safe_fat_path("/iTunes_Control/iTunes/iTunesCDB")
        with self.assertRaises(Refused):
            assert_safe_fat_path("/iPod_Control/iTunes/iTunesCDB")
        src = Path(self.td.name) / "db"
        src.write_bytes(b"nope")
        with self.assertRaises(Refused) as ctx:
            copy_verified(str(self.img), src, "/iTunes_Control/foo")
        self.assertIn("iTunes", str(ctx.exception))


class FatInstallOnImageTests(unittest.TestCase):
    """Copy the real hashed loader files onto a FAT disk image (no hardware)."""

    def setUp(self):
        if not shutil.which("mkfs.vfat") or not shutil.which("mcopy"):
            self.skipTest("mtools/mkfs.vfat required")
        self.td = tempfile.TemporaryDirectory()
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        self.img = Path(self.td.name) / "fat.img"
        subprocess.check_call(
            ["dd", "if=/dev/zero", f"of={self.img}", "bs=1M", "count=32", "status=none"]
        )
        subprocess.check_call(
            ["mkfs.vfat", "-F", "32", "-n", "IPOD", str(self.img)], stdout=subprocess.DEVNULL
        )
        self.size = self.img.stat().st_size
        self.backup = Path(self.td.name) / "nano.img"
        self.backup.write_bytes(b"B" * self.size)
        self.bhash = sha256_file(self.backup)

    def tearDown(self):
        self.td.cleanup()

    def test_install_loader_files_on_image(self):
        nano = _nano(
            device=str(self.img),
            raw_device=str(self.img),
            partition=str(self.img),
            usb_serial="IMGSERIAL1",
            size=self.size,
            fstype="vfat",
            mountpoints=[],
        )
        with mock.patch("host.untether.unmount"):
            rec = install_loader_files(
                nano,
                backup=self.backup,
                backup_sha256=self.bhash,
                allow_size=self.size,
                confirm_untested=True,
            )
        self.assertEqual(rec["verified"], "yes")
        self.assertEqual(rec["resident_sha256"], RESIDENT_SHA256)
        self.assertTrue(exists(str(self.img), "/Apps/Startup/resident.hbapp"))
        self.assertTrue(exists(str(self.img), "/Apps/Startup/ab.hbapp"))
        self.assertFalse(exists(str(self.img), "/Apps/Startup/resident-B.active"))

    def test_wrong_image_size_refused_before_write(self):
        nano = _nano(
            partition=str(self.img),
            usb_serial="IMGSERIAL1",
            size=12345,
            fstype="vfat",
        )
        with mock.patch("host.untether.unmount"):
            with self.assertRaises(Refused) as ctx:
                install_loader_files(
                    nano,
                    backup=self.backup,
                    backup_sha256=self.bhash,
                )
        self.assertIn("--allow-disk-size", str(ctx.exception))
        self.assertFalse(exists(str(self.img), "/Apps/Startup/resident.hbapp"))


class BackupSkipTests(unittest.TestCase):
    def test_file_present_is_always_hashed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nano.img"
            p.write_bytes(b"abc")
            digest = sha256_file(p)
            with self.assertRaises(Refused):
                check_backup(p, "0" * 64, 3, skip_file=True)
            self.assertEqual(check_backup(p, digest, 3, skip_file=True), digest)

    def test_missing_file_skip_requires_hex(self):
        missing = Path("/no/such/backup.img")
        with self.assertRaises(Refused):
            check_backup(missing, "0" * 64, 99, skip_file=False)
        self.assertEqual(check_backup(missing, "ab" * 32, 99, skip_file=True), "ab" * 32)
        with self.assertRaises(Refused):
            check_backup(missing, "not-a-hash", 99, skip_file=True)


class AbReceiptTests(unittest.TestCase):
    def test_ok(self):
        rec = {
            "verified": "yes",
            "active_slot": "A",
            "ab_sha256": AB_SHA256,
            "slot_a_sha256": SLOT_A_SHA256,
            "resident_sha256": RESIDENT_SHA256,
            "usb_serial": "USB1",
        }
        check_ab_receipt(rec, "USB1")
        check_ab_receipt(rec, None)

    def test_wrong_slot_refused(self):
        with self.assertRaises(Refused):
            check_ab_receipt({"verified": "yes", "active_slot": "B"})

    def test_hash_fields_required(self):
        rec = {
            "verified": "yes",
            "active_slot": "A",
            "ab_sha256": "0" * 64,
            "slot_a_sha256": SLOT_A_SHA256,
            "resident_sha256": RESIDENT_SHA256,
            "usb_serial": "X",
        }
        with self.assertRaises(Refused) as ctx:
            check_ab_receipt(rec, "X")
        self.assertIn("selector hash", str(ctx.exception))

    def test_usb_serial_mismatch(self):
        rec = {
            "verified": "yes",
            "active_slot": "A",
            "ab_sha256": AB_SHA256,
            "slot_a_sha256": SLOT_A_SHA256,
            "resident_sha256": RESIDENT_SHA256,
            "usb_serial": "ONE",
        }
        with self.assertRaises(Refused):
            check_ab_receipt(rec, "TWO")

    def test_missing_usb_serial_field(self):
        rec = {
            "verified": "yes",
            "active_slot": "A",
            "ab_sha256": AB_SHA256,
            "slot_a_sha256": SLOT_A_SHA256,
            "resident_sha256": RESIDENT_SHA256,
        }
        with self.assertRaises(Refused) as ctx:
            check_ab_receipt(rec, None)
        self.assertIn("usb_serial", str(ctx.exception))


class UsbSerialTests(unittest.TestCase):
    def test_disk_name_refused(self):
        with self.assertRaises(Refused):
            check_usb_serial("disk9")
        with self.assertRaises(Refused):
            check_usb_serial("rdisk2")
        with self.assertRaises(Refused):
            check_usb_serial("unknown")
        self.assertEqual(check_usb_serial("CCQK123456"), "CCQK123456")

    def test_product_string_refused(self):
        with self.assertRaises(Refused) as ctx:
            check_usb_serial("Apple iPod Media")
        self.assertIn("product", str(ctx.exception).lower())
        with self.assertRaises(Refused):
            check_usb_serial("iPod Media")


class PrepareLogTests(unittest.TestCase):
    def test_exact_line_serial_not_prefix(self):
        log = (
            'Found device device="Nano 7G" interface=diskmode\n'
            "SerialNumber: SNFLASH1\n"
            "Recovery mode ready; no device partitions were written\n"
        )
        check_prepare_log(log, "SNFLASH1")
        with self.assertRaises(Refused) as ctx:
            check_prepare_log(log, "SNFLASH")
        self.assertIn("serial mismatch", str(ctx.exception).lower())

    def test_split_found_device_refused(self):
        log = (
            "Found device\n"
            'device="Nano 7G"\n'
            "interface=diskmode\n"
            "SerialNumber: ABC\n"
            "Recovery mode ready; no device partitions were written\n"
        )
        with self.assertRaises(Refused) as ctx:
            check_prepare_log(log, "ABC")
        self.assertIn("diskmode", str(ctx.exception).lower())

    def test_2015_prepare_line_refused_without_flag(self):
        log = (
            'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
            "SerialNumber: SNFLASH1\n"
            "Recovery mode ready; no device partitions were written\n"
        )
        with self.assertRaises(Refused) as ctx:
            check_prepare_log(log, "SNFLASH1")
        self.assertIn("2015", str(ctx.exception).lower())
        self.assertIn("124", str(ctx.exception).lower())

    def test_2015_prepare_line_ok_with_flag(self):
        log = (
            'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
            "SerialNumber: SNFLASH1\n"
            "Recovery mode ready; no device partitions were written\n"
        )
        check_prepare_log(log, "SNFLASH1", accept_2015=True)

    def test_2012_line_plus_late_usb_refused(self):
        log = (
            'Found device device="Nano 7G" interface=diskmode\n'
            "SerialNumber: SNFLASH1\n"
            "Recovery mode ready; no device partitions were written\n"
        )
        check_prepare_log(log, "SNFLASH1")
        with self.assertRaises(Refused):
            check_prepare_log(log, "SNFLASH1", late_usb=True)
        check_prepare_log(log, "SNFLASH1", late_usb=True, accept_2015=True)

    def test_prepare_restore_polls_124a(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["NANO7_STATE_DIR"] = td
            os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(td) / "cfg")
            wind3x = _fake_wind3x(Path(td), serial="SNFLASH1")
            with mock.patch(
                "host.usb.list_apple_usb",
                return_value=[(0x05AC, 0x124A, "late")],
            ):
                with self.assertRaises(Refused) as ctx:
                    prepare_restore(wind3x, "SNFLASH1")
                self.assertIn("2015", str(ctx.exception).lower())
                log = prepare_restore(wind3x, "SNFLASH1", accept_2015=True)
            self.assertIn("Nano 7G", log)


class HfsRefusalTests(unittest.TestCase):
    def test_hfs_refused_before_write(self):
        nano = _nano(fstype="hfs")
        with tempfile.TemporaryDirectory() as td:
            os.environ["NANO7_STATE_DIR"] = td
            os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(td) / "cfg")
            backup = Path(td) / "b.img"
            backup.write_bytes(b"x" * 8)
            with mock.patch("host.untether.need_mtools"), mock.patch(
                "host.untether.verify_release_artifacts"
            ), mock.patch("host.untether.unmount"), mock.patch(
                "host.untether.check_backup", return_value="a" * 64
            ):
                with self.assertRaises(Refused) as ctx:
                    install_loader_files(
                        nano,
                        backup=backup,
                        backup_sha256="a" * 64,
                    )
                self.assertIn("HFS+", str(ctx.exception))
                self.assertIn("fat32", str(ctx.exception).lower())
                self.assertNotIn("iTunes on Windows", str(ctx.exception))

    def test_assert_not_hfs_covers_stage(self):
        with self.assertRaises(Refused):
            assert_not_hfs(_nano(fstype="hfs"))
        with self.assertRaises(Refused):
            assert_not_hfs(_nano(fstype="Apple_HFSX"))
        assert_not_hfs(_nano(fstype="vfat"))


class MacDiskutilMockTests(unittest.TestCase):
    def _run_find(self, listing, info):
        def fake_check_output(cmd, **kwargs):
            if cmd[:3] == ["diskutil", "list", "-plist"]:
                return plistlib.dumps(listing)
            if cmd[:3] == ["diskutil", "info", "-plist"]:
                return plistlib.dumps(info)
            raise AssertionError(cmd)

        with mock.patch("host.device.is_macos", return_value=True), mock.patch(
            "host.device.is_linux", return_value=False
        ), mock.patch("host.device.subprocess.check_output", side_effect=fake_check_output):
            return find_nanos()

    def test_find_macos_parses_plist(self):
        ident = "disk9"
        listing = {
            "AllDisksAndPartitions": [
                {
                    "DeviceIdentifier": ident,
                    "Size": TESTED_DISK_SIZE,
                    "Partitions": [
                        {
                            "DeviceIdentifier": "disk9s1",
                            "Content": "DOS_FAT_32",
                            "MountPoint": "/Volumes/IPOD",
                        }
                    ],
                }
            ]
        }
        info = {
            "TotalSize": TESTED_DISK_SIZE,
            "MediaName": "iPod",
            "BusProtocol": "USB",
            "FilesystemName": "MS-DOS (FAT32)",
            "USB Serial Number": "MACMOCK1",
            "MountPoint": "",
        }
        found = self._run_find(listing, info)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].raw_device, "/dev/rdisk9")
        self.assertEqual(found[0].partition, "/dev/rdisk9s1")
        self.assertEqual(found[0].fstype, "vfat")
        self.assertEqual(found[0].usb_serial, "MACMOCK1")
        self.assertTrue(found[0].hardware_tested_size)

    def test_iphone_not_detected(self):
        listing = {
            "AllDisksAndPartitions": [
                {
                    "DeviceIdentifier": "disk8",
                    "Size": TESTED_DISK_SIZE,
                    "Partitions": [{"DeviceIdentifier": "disk8s1", "Content": "DOS_FAT_32"}],
                }
            ]
        }
        info = {
            "TotalSize": TESTED_DISK_SIZE,
            "MediaName": "iPhone",
            "DeviceVendor": "Apple Inc.",
            "BusProtocol": "USB",
            "USB Serial Number": "IPHONE1",
        }
        self.assertEqual(self._run_find(listing, info), [])

    def test_volume_name_ipod_is_enough(self):
        listing = {
            "AllDisksAndPartitions": [
                {
                    "DeviceIdentifier": "disk7",
                    "Size": TESTED_DISK_SIZE,
                    "VolumeName": "IPOD",
                    "Partitions": [{"DeviceIdentifier": "disk7s1", "Content": "DOS_FAT_32"}],
                }
            ]
        }
        info = {
            "TotalSize": TESTED_DISK_SIZE,
            "BusProtocol": "USB",
            "VolumeName": "IPOD",
            "USB Serial Number": "VOLIPOD1",
            "FilesystemName": "MS-DOS (FAT32)",
        }
        found = self._run_find(listing, info)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].usb_serial, "VOLIPOD1")

    def test_find_macos_prefers_fat_over_firmware_slice(self):
        ident = "disk9"
        listing = {
            "AllDisksAndPartitions": [
                {
                    "DeviceIdentifier": ident,
                    "Size": TESTED_DISK_SIZE,
                    "Partitions": [
                        {
                            "DeviceIdentifier": "disk9s1",
                            "Content": "Apple_MDFW",
                        },
                        {
                            "DeviceIdentifier": "disk9s2",
                            "Content": "DOS_FAT_32",
                            "MountPoint": "/Volumes/IPOD",
                        },
                    ],
                }
            ]
        }
        info = {
            "TotalSize": TESTED_DISK_SIZE,
            "MediaName": "iPod",
            "BusProtocol": "USB",
            "FilesystemName": "MS-DOS (FAT32)",
            "USB Serial Number": "WINPOD1",
        }
        found = self._run_find(listing, info)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].partition, "/dev/rdisk9s2")
        self.assertEqual(found[0].fstype, "vfat")
        self.assertEqual(found[0].usb_serial, "WINPOD1")

    def test_find_macos_uses_ioreg_serial_not_product_name(self):
        ident = "disk12"
        listing = {
            "AllDisksAndPartitions": [
                {
                    "DeviceIdentifier": ident,
                    "Size": TESTED_DISK_SIZE,
                    "Partitions": [
                        {
                            "DeviceIdentifier": "disk12s1",
                            "Content": "Apple_HFS",
                            "MountPoint": "/Volumes/iPod",
                        }
                    ],
                }
            ]
        }
        info = {
            "TotalSize": TESTED_DISK_SIZE,
            "MediaName": "Apple iPod Media",
            "IORegistryEntryName": "Apple iPod Media",
            "USB Product Name": "Apple iPod Media",
            "BusProtocol": "USB",
            "FilesystemName": "HFS+",
            "Content": "Apple_HFS",
        }
        ioreg = (
            '+-o Root  <class IORegistryExplorer>\n'
            '  +-o iPod@00100000  <class IOUSBHostDevice, id 0x1, registered, matched, active, busy 0, retain 8>\n'
            '    {\n'
            '      "idVendor" = 1452\n'
            '      "idProduct" = 4711\n'
            '      "USB Product Name" = "Apple iPod Media"\n'
            '      "USB Serial Number" = "CCQKIOREGSERIAL"\n'
            '    }\n'
        )

        def fake_check_output(cmd, **kwargs):
            if cmd[:3] == ["diskutil", "list", "-plist"]:
                return plistlib.dumps(listing)
            if cmd[:3] == ["diskutil", "info", "-plist"]:
                return plistlib.dumps(info)
            raise AssertionError(cmd)

        with mock.patch("host.device.is_macos", return_value=True), mock.patch(
            "host.device.is_linux", return_value=False
        ), mock.patch(
            "host.device.subprocess.check_output", side_effect=fake_check_output
        ), mock.patch("host.device.run_out", return_value=ioreg):
            found = find_nanos()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].usb_serial, "CCQKIOREGSERIAL")
        self.assertNotEqual(found[0].usb_serial, "Apple iPod Media")
        self.assertEqual(found[0].device, "/dev/disk12")
        self.assertEqual(found[0].fstype, "hfs")

    def test_find_macos_unknown_when_ioreg_has_no_serial(self):
        ident = "disk12"
        listing = {
            "AllDisksAndPartitions": [
                {
                    "DeviceIdentifier": ident,
                    "Size": TESTED_DISK_SIZE,
                    "Partitions": [
                        {
                            "DeviceIdentifier": "disk12s1",
                            "Content": "Apple_HFS",
                        }
                    ],
                }
            ]
        }
        info = {
            "TotalSize": TESTED_DISK_SIZE,
            "MediaName": "Apple iPod Media",
            "IORegistryEntryName": "Apple iPod Media",
            "BusProtocol": "USB",
            "FilesystemName": "HFS+",
        }

        def fake_check_output(cmd, **kwargs):
            if cmd[:3] == ["diskutil", "list", "-plist"]:
                return plistlib.dumps(listing)
            if cmd[:3] == ["diskutil", "info", "-plist"]:
                return plistlib.dumps(info)
            if cmd[:2] == ["system_profiler", "SPUSBDataType"]:
                return plistlib.dumps([])
            raise AssertionError(cmd)

        with mock.patch("host.device.is_macos", return_value=True), mock.patch(
            "host.device.is_linux", return_value=False
        ), mock.patch(
            "host.device.subprocess.check_output", side_effect=fake_check_output
        ), mock.patch("host.device.run_out", return_value=""):
            found = find_nanos()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].usb_serial, "unknown")


class IoregUsbParseTests(unittest.TestCase):
    def test_parses_05ac_1267_serial(self):
        text = (
            '+-o iPod@14100000  <class IOUSBHostDevice>\n'
            '  {\n'
            '    "idVendor" = 1452\n'
            '    "idProduct" = 4711\n'
            '    "USB Product Name" = "Apple iPod Media"\n'
            '    "USB Serial Number" = "CCQKFROMIOREG"\n'
            '  }\n'
        )
        devs = parse_ioreg_usb(text)
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["vid"], 0x05AC)
        self.assertEqual(devs[0]["pid"], 0x1267)
        self.assertEqual(devs[0]["serial"], "CCQKFROMIOREG")
        self.assertEqual(devs[0]["product"], "Apple iPod Media")
        from host.usb import apple_usb_serials_for_pid
        from host.const import NANO7_DISK_PID

        self.assertEqual(
            apple_usb_serials_for_pid(text, NANO7_DISK_PID), ["CCQKFROMIOREG"]
        )


class FlashMockTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        self.addCleanup(self.td.cleanup)
        self.mse = Path(self.td.name) / "Firmware-untethered.MSE"
        self.mse.write_bytes(b"mse-stub")
        self.backup = Path(self.td.name) / "nano.img"
        self.backup.write_bytes(b"Z" * 64)
        self.bhash = sha256_file(self.backup)
        self.wind3x = _fake_wind3x(Path(self.td.name), serial="SNFLASH1")
        _ab_receipt(Path(self.td.name), usb_serial="USB1")

    def _flash(self, **kw):
        nano = kw.pop("nano", _nano(usb_serial="USB1", size=64))
        defaults = dict(
            mse=self.mse,
            backup=self.backup,
            backup_sha256=self.bhash,
            serial="SNFLASH1",
            dry_run=True,
            allow_size=64,
            confirm_untested=True,
            accept_untested_flasher=True,
        )
        defaults.update(kw)
        with mock.patch("host.untether.verify_release_artifacts"), mock.patch(
            "host.untether.check_mse", return_value=FIRMWARE_MSE_SHA256
        ), mock.patch("host.safety.check_mse", return_value=FIRMWARE_MSE_SHA256), mock.patch(
            "host.untether.select_wind3x", return_value=(self.wind3x, False, "fake")
        ), mock.patch("host.untether.wait_dfu"):
            return flash_firmware(nano, **defaults)

    def test_missing_serial_refused(self):
        with self.assertRaises(Refused) as ctx:
            self._flash(serial="")
        self.assertIn("serial", str(ctx.exception).lower())

    def test_wrong_backup_hash_refused(self):
        with self.assertRaises(Refused) as ctx:
            self._flash(backup_sha256="0" * 64)
        self.assertIn("hash mismatch", str(ctx.exception))

    def test_wrong_size_refused_without_override(self):
        nano = _nano(usb_serial="USB1", size=12345)
        with self.assertRaises(Refused) as ctx:
            self._flash(nano=nano, allow_size=None, confirm_untested=False)
        self.assertIn("--allow-disk-size", str(ctx.exception))

    def test_untested_flasher_refused_without_flag(self):
        with self.assertRaises(Refused) as ctx:
            self._flash(accept_untested_flasher=False)
        self.assertIn("untested", str(ctx.exception).lower())

    def test_2015_usb_refused_without_flag(self):
        with mock.patch(
            "host.usb.list_apple_usb",
            return_value=[(0x05AC, 0x124A, "late")],
        ):
            with self.assertRaises(Refused) as ctx:
                self._flash()
        self.assertIn("2015", str(ctx.exception).lower())

    def test_2015_flag_allows_dry_run(self):
        with mock.patch(
            "host.usb.list_apple_usb",
            return_value=[(0x05AC, 0x124A, "late")],
        ):
            out = self._flash(accept_2015_refresh=True)
        self.assertTrue(out["dry_run"])

    def test_dry_run_ok_and_repeat_write_refused(self):
        out = self._flash(dry_run=True)
        self.assertTrue(out["dry_run"])
        self._flash(dry_run=False, accept_brick=True)
        with self.assertRaises(Refused) as ctx:
            self._flash(dry_run=False, accept_brick=True)
        self.assertIn("already attempted", str(ctx.exception))
        with self.assertRaises(Refused) as ctx:
            self._flash(dry_run=True)
        self.assertIn("already attempted", str(ctx.exception))

    def test_serial_mismatch_on_prepare(self):
        with self.assertRaises(Refused) as ctx:
            self._flash(serial="OTHER")
        self.assertIn("serial mismatch", str(ctx.exception).lower())

    def test_serial_prefix_does_not_match(self):
        with self.assertRaises(Refused) as ctx:
            self._flash(serial="SNFLASH")
        self.assertIn("serial mismatch", str(ctx.exception).lower())


class SshLocalhostMockTests(unittest.TestCase):
    """SSH remote mode against localhost using a fake ssh/sudo that exec locally."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        root = Path(self.td.name)
        self.xdg = root / "xdg"
        self.state = root / "state"
        self.cfg = root / "cfg"
        self.bindir = root / "bin"
        self.bindir.mkdir()
        os.environ["XDG_CONFIG_HOME"] = str(self.xdg)
        os.environ["NANO7_STATE_DIR"] = str(self.state)
        os.environ["NANO7KIT_CONFIG_DIR"] = str(self.cfg)
        self._old_path = os.environ["PATH"]
        os.environ["PATH"] = str(self.bindir) + os.pathsep + self._old_path
        self.addCleanup(lambda: os.environ.__setitem__("PATH", self._old_path))

        ssh = self.bindir / "ssh"
        ssh.write_text(
            "#!/bin/sh\n"
            'while [ $# -gt 0 ]; do\n'
            '  case "$1" in\n'
            "    -o) shift 2 ;;\n"
            "    -t|-q|-n) shift ;;\n"
            "    -l) shift 2 ;;\n"
            "    --) shift; break ;;\n"
            "    -*) shift ;;\n"
            "    *) break ;;\n"
            "  esac\n"
            "done\n"
            "shift\n"  # host
            'exec /bin/sh -c "$*"\n'
        )
        ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)
        sudo = self.bindir / "sudo"
        sudo.write_text(
            "#!/bin/sh\n"
            'while [ "$1" = "-n" ] || [ "$1" = "-E" ]; do shift; done\n'
            'if [ "$1" = "env" ]; then\n'
            "  shift\n"
            '  while [ $# -gt 0 ]; do\n'
            '    case "$1" in\n'
            '      *=*) export "$1"; shift ;;\n'
            "      *) break ;;\n"
            "    esac\n"
            "  done\n"
            "fi\n"
            'exec "$@"\n'
        )
        sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)

        repo = Path(__file__).resolve().parent.parent
        self.agent_root = root / "agent"
        (self.agent_root / "bin").mkdir(parents=True)
        agent = self.agent_root / "bin" / "nano7kit"
        agent.write_text(
            "#!/bin/sh\n"
            f"export PYTHONPATH={repo}\n"
            f"export NANO7KIT_ROOT={repo}\n"
            f"export NANO7_STATE_DIR={self.state}\n"
            f"export NANO7KIT_CONFIG_DIR={self.cfg}\n"
            f"export XDG_CONFIG_HOME={self.xdg}\n"
            "export NANO7KIT_SKIP_SUDO=1\n"
            f'exec {sys.executable} -m host "$@"\n'
        )
        agent.chmod(agent.stat().st_mode | stat.S_IXUSR)
        from host.state import save_config

        save_config(
            {
                "MODE": "ssh",
                "REMOTE": "localhost",
                "REMOTE_ROOT": str(self.agent_root),
            }
        )

    def test_ssh_ok_and_doctor(self):
        from host.remote import remote_python, ssh_ok

        self.assertTrue(ssh_ok("localhost"))
        r = remote_python("localhost", ["doctor"], sudo=True, remote_root=str(self.agent_root))
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("hardware-tested", r.stdout)
        self.assertNotIn("\u2014", r.stdout)

    def test_flash_without_backup_refused(self):
        from host.remote import remote_python

        r = remote_python(
            "localhost",
            ["flash", "--dry-run", "--serial", "ABC"],
            sudo=True,
            remote_root=str(self.agent_root),
        )
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertIn("REFUSED", r.stderr)

    def test_flash_missing_serial_refused(self):
        from host.remote import remote_python
        from host.state import remember_backup

        backup = Path(self.td.name) / "b.img"
        backup.write_bytes(b"x" * 64)
        remember_backup("USB1", backup, sha256_file(backup), 64)
        r = remote_python(
            "localhost",
            [
                "--allow-disk-size",
                "64",
                "--confirm-untested-capacity",
                "flash",
                "--dry-run",
                "--backup",
                str(backup),
                "--backup-sha256",
                sha256_file(backup),
            ],
            sudo=True,
            remote_root=str(self.agent_root),
        )
        self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
        self.assertIn("REFUSED", r.stderr)
        self.assertIn("serial", r.stderr.lower())


class DiskSizeAllowListTests(unittest.TestCase):
    def test_override_does_not_skip_hash_requirement(self):
        # Size override is allowed, but backup hash is still required by check_backup.
        info = check_disk_size(64, allow_size=64, confirm_untested=True)
        self.assertTrue(info.get("override"))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nano.img"
            p.write_bytes(b"Z" * 64)
            with self.assertRaises(Refused):
                check_backup(p, "0" * 64, 64)


if __name__ == "__main__":
    unittest.main()
