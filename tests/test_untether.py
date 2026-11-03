#!/usr/bin/env python3
"""Firmware-only 2015 untether path: size gate, argv, typed UNTETHER, verify."""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host import const
from host.compat import Refused, sha256_file
from host.const import IPODREPARTITION_DISK_SIZE, TESTED_DISK_SIZE
from host.device import Nano, annotate_size
from host.layout import inspect_bytes
from host.usb import assert_firmware_only_log, firmware_only_argv
from host.untether import (
    UNTETHER_VERIFY_INCONCLUSIVE,
    UNTETHER_VERIFY_OK,
    apps_markers,
    check_loader_hashes,
    check_untether_device,
    run_untether_command,
    verify_untether,
    volume_root_listing,
    write_untether_firmware,
)

START = ROOT / "start"
DISK_SIZE_SH = ROOT / "untether" / "scripts" / "disk-size.sh"
SG_RAW_C = ROOT / "host" / "cmd" / "ipod_sg_raw" / "ipod_sg_raw.c"


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


def _ipodrepartition_mbr() -> bytes:
    data = bytearray(512)
    data[510] = 0x55
    data[511] = 0xAA
    data[0x1BE + 4] = 0x0C
    struct.pack_into("<I", data, 0x1BE + 8, 64)
    struct.pack_into("<I", data, 0x1BE + 12, 3_858_975)
    return bytes(data)


def _ident() -> dict:
    return {
        "sha256": const.FIRMWARE_MSE_SHA256,
        "identity": "39A10023",
        "osos_length": 12_000_000,
        "rsrc_format": 0x03,
        "label": "39A10023 Helvetica CFF (ipod_sun --device=nano7-refresh)",
    }


def _plant_tested_apps(vol: Path) -> None:
    src = ROOT / "untether" / "artifacts" / "device"
    dest = vol / "Apps"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


class FirmwareOnlyArgvTests(unittest.TestCase):
    def test_argv_is_restore_firmware_only(self):
        argv = firmware_only_argv(Path("/tmp/Firmware-untethered.MSE"))
        self.assertEqual(argv[0], "restore")
        self.assertEqual(argv[1], "--firmware")
        self.assertNotIn("--full", argv)
        self.assertNotIn("--bootloader", argv)
        self.assertNotIn("--replace", argv)
        self.assertNotIn("nand", " ".join(argv).lower())

    def test_log_requires_one_firmware_write_no_bootloader(self):
        good = "Writing firmware...\nResetting...\n"
        assert_firmware_only_log(good)
        with self.assertRaises(Refused) as ctx:
            assert_firmware_only_log("Writing firmware...\nWriting firmware...\nResetting...\n")
        self.assertIn("exactly one", str(ctx.exception))
        with self.assertRaises(Refused) as ctx:
            assert_firmware_only_log("Writing firmware...\nWriting bootloader...\nResetting...\n")
        self.assertIn("bootloader", str(ctx.exception).lower())
        with self.assertRaises(Refused) as ctx:
            assert_firmware_only_log(
                "Writing firmware...\nReformatting system partition...\nResetting...\n"
            )
        self.assertIn("IPodRepartition", str(ctx.exception) + "IPodRepartition")
        with self.assertRaises(Refused) as ctx:
            assert_firmware_only_log("Writing firmware...\nIPodRepartition\nResetting...\n")
        self.assertIn("IPodRepartition", str(ctx.exception))
        with self.assertRaises(Refused):
            assert_firmware_only_log("Writing firmware...\n")

    def test_flash_restore_calls_firmware_only_argv(self):
        from host.usb import flash_restore

        seen = {}

        def fake_run(args, wind3x):
            seen["args"] = list(args)
            seen["wind3x"] = wind3x
            log = "Writing firmware...\nResetting...\n"
            return subprocess.CompletedProcess(args, 0, log, "")

        with tempfile.TemporaryDirectory() as td:
            os.environ["NANO7_STATE_DIR"] = td
            mse = Path(td) / "Firmware-untethered.MSE"
            mse.write_bytes(b"mse")
            with mock.patch("host.usb.run_wind3x", side_effect=fake_run):
                flash_restore(Path("/tmp/wInd3x-fake"), mse)
        self.assertEqual(seen["args"][0], "restore")
        self.assertEqual(seen["args"][1], "--firmware")
        self.assertEqual(seen["args"][2], str(mse))
        self.assertNotIn("--full", seen["args"])
        self.assertNotIn("--bootloader", seen["args"])


class IpodrepartitionLayoutTests(unittest.TestCase):
    def test_mbr_prefix_is_4k_fat32_fill(self):
        layout = inspect_bytes(_ipodrepartition_mbr(), IPODREPARTITION_DISK_SIZE)
        self.assertTrue(layout.is_ipodrepartition_4k_fat32())
        self.assertEqual(layout.sector_size, 4096)
        self.assertEqual(layout.data.type_id, 0x0C)
        self.assertEqual(layout.data.start_lba, 64)
        self.assertIsNone(layout.firmware)

    def test_wrong_type_is_not_ipodrepartition_4k(self):
        raw = bytearray(_ipodrepartition_mbr())
        raw[0x1BE + 4] = 0x0B
        layout = inspect_bytes(bytes(raw), IPODREPARTITION_DISK_SIZE)
        self.assertFalse(layout.is_ipodrepartition_4k_fat32())


class DiskSizeScriptTests(unittest.TestCase):
    def test_accept_nano_size_script(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good.mbr"
            good.write_bytes(_ipodrepartition_mbr())
            bad = Path(td) / "bad.mbr"
            blob = bytearray(_ipodrepartition_mbr())
            blob[0x1BE + 4] = 0x07
            bad.write_bytes(bytes(blob))
            script = f"""
set -e
. "{DISK_SIZE_SH}"
accept_nano_size 15798411264 && echo TESTED_OK
accept_nano_size 15806623744 "{good}" && echo RECOVERY_OK
if accept_nano_size 15806623744 "{bad}"; then echo BAD_ACCEPTED; exit 1; fi
echo MISMATCH_REFUSED
if accept_nano_size 12345 /dev/null; then echo UNKNOWN_ACCEPTED; exit 1; fi
echo UNKNOWN_REFUSED
"""
            r = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
            )
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("TESTED_OK", r.stdout)
            self.assertIn("RECOVERY_OK", r.stdout)
            self.assertIn("MISMATCH_REFUSED", r.stdout)
            self.assertIn("UNKNOWN_REFUSED", r.stdout)


class UntetherCommandTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        self.mse = Path(self.td.name) / "Firmware-untethered.MSE"
        self.mse.write_bytes(b"mse-stub")
        self.backup = Path(self.td.name) / "nano.img"
        self.backup.write_bytes(_ipodrepartition_mbr() + b"\0" * 1024)
        self.bhash = sha256_file(self.backup)

    def test_replace_refused(self):
        with self.assertRaises(Refused) as ctx:
            run_untether_command(
                _nano(),
                check_only=True,
                run=False,
                verify=False,
                mse=self.mse,
                serial="XYZ123ABC456",
                replace=True,
            )
        self.assertIn("--replace", str(ctx.exception))

    def test_check_only_never_writes_or_enters_dfu(self):
        def boom(*a, **k):
            raise AssertionError("write/DFU path must not run on --check-only")

        nano = _nano()
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = False
        layout.scheme = "mbr"
        layout.summary.return_value = "scheme mbr tested Finder map"
        with mock.patch("host.untether.verify_release_artifacts"), mock.patch(
            "host.fat.assert_not_hfs"
        ), mock.patch("host.untether.check_usb_serial"), mock.patch(
            "host.untether._layout_for_nano", return_value=layout
        ), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch(
            "host.untether.check_mse", return_value=const.FIRMWARE_MSE_SHA256
        ), mock.patch(
            "host.mse.inspect_mse_flash_identity", return_value=_ident()
        ), mock.patch(
            "host.usb.select_wind3x",
            return_value=(Path("/tmp/wInd3x-fake"), False, "fake"),
        ), mock.patch("host.usb.wait_dfu", side_effect=boom), mock.patch(
            "host.usb.flash_restore", side_effect=boom
        ):
            out = run_untether_command(
                nano,
                check_only=True,
                run=False,
                verify=False,
                mse=self.mse,
                serial="XYZ123ABC456",
                accept_2015_refresh=True,
                accept_untested_flasher=True,
            )
        self.assertTrue(out["check_only"])
        self.assertEqual(out["identity"], "39A10023")
        self.assertEqual(out["argv"][0:2], ["restore", "--firmware"])
        self.assertNotIn("--full", out["argv"])
        self.assertNotIn("--bootloader", out["argv"])

    def test_run_writes_firmware_only_with_typed_untether(self):
        seen = {}

        def fake_flash(wind3x, mse):
            seen["mse"] = mse
            seen["wind3x"] = wind3x
            return "Writing firmware...\nResetting...\n"

        def fake_loader(*_a, **kw):
            seen["leave_mounted"] = kw.get("leave_mounted")
            return {}

        nano = _nano()
        with mock.patch(
            "host.untether.check_untether_device",
            return_value={"check_only": True, "identity": "39A10023"},
        ), mock.patch(
            "host.untether.check_mse", return_value=const.FIRMWARE_MSE_SHA256
        ), mock.patch(
            "host.mse.inspect_mse_flash_identity", return_value=_ident()
        ), mock.patch("host.untether.check_backup"), mock.patch(
            "host.untether.install_loader_files", side_effect=fake_loader
        ), mock.patch(
            "host.usb.late_wtf_present", return_value=False
        ), mock.patch(
            "host.usb.select_wind3x",
            return_value=(Path("/tmp/wInd3x-fake"), False, "fake"),
        ), mock.patch("host.usb.wait_dfu"), mock.patch(
            "host.usb.prepare_restore",
            return_value="SerialNumber: XYZ123ABC456\n",
        ), mock.patch(
            "host.usb.parse_serial", return_value="XYZ123ABC456"
        ), mock.patch("host.usb.flash_restore", side_effect=fake_flash):
            out = write_untether_firmware(
                nano,
                mse=self.mse,
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_sha256=self.bhash,
                accept_2015_refresh=True,
                accept_untested_flasher=True,
                typed_confirm="UNTETHER",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["argv"][0:2], ["restore", "--firmware"])
        self.assertIs(seen["mse"], self.mse)
        self.assertIs(seen.get("leave_mounted"), False)

    def test_run_refuses_wrong_typed_word(self):
        nano = _nano()
        with mock.patch(
            "host.untether.check_untether_device", return_value={"check_only": True}
        ), mock.patch(
            "host.untether.check_mse", return_value=const.FIRMWARE_MSE_SHA256
        ), mock.patch(
            "host.mse.inspect_mse_flash_identity", return_value=_ident()
        ), mock.patch("host.untether.check_backup"), mock.patch(
            "host.untether.install_loader_files", return_value={}
        ), mock.patch(
            "host.usb.late_wtf_present", return_value=False
        ), mock.patch(
            "host.usb.select_wind3x",
            return_value=(Path("/tmp/wInd3x-fake"), False, "fake"),
        ):
            with self.assertRaises(Refused) as ctx:
                write_untether_firmware(
                    nano,
                    mse=self.mse,
                    serial="XYZ123ABC456",
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    accept_untested_flasher=True,
                    typed_confirm="FLASH",
                )
        self.assertIn("UNTETHER", str(ctx.exception))

    def test_second_run_refused(self):
        nano = _nano()
        with mock.patch(
            "host.untether.check_untether_device", return_value={"check_only": True}
        ), mock.patch(
            "host.untether.check_mse", return_value=const.FIRMWARE_MSE_SHA256
        ), mock.patch(
            "host.mse.inspect_mse_flash_identity", return_value=_ident()
        ), mock.patch("host.untether.check_backup"), mock.patch(
            "host.untether.install_loader_files", return_value={}
        ), mock.patch(
            "host.usb.late_wtf_present", return_value=False
        ), mock.patch(
            "host.usb.select_wind3x",
            return_value=(Path("/tmp/wInd3x-fake"), False, "fake"),
        ), mock.patch("host.usb.wait_dfu"), mock.patch(
            "host.usb.prepare_restore",
            return_value="SerialNumber: XYZ123ABC456\n",
        ), mock.patch(
            "host.usb.parse_serial", return_value="XYZ123ABC456"
        ), mock.patch(
            "host.usb.flash_restore",
            return_value="Writing firmware...\nResetting...\n",
        ):
            write_untether_firmware(
                nano,
                mse=self.mse,
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_sha256=self.bhash,
                accept_untested_flasher=True,
                typed_confirm="UNTETHER",
            )
            with self.assertRaises(Refused) as ctx:
                write_untether_firmware(
                    nano,
                    mse=self.mse,
                    serial="XYZ123ABC456",
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    accept_untested_flasher=True,
                    typed_confirm="UNTETHER",
                )
        self.assertIn("already attempted", str(ctx.exception).lower())

    def test_verify_refuses_when_harness_rejected(self):
        nano = _nano()
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = False
        layout.scheme = "mbr"
        layout.summary.return_value = "scheme mbr tested Finder map"
        with mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.check_usb_serial"
        ), mock.patch("host.untether._layout_for_nano", return_value=layout), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch(
            "host.scsi.harness_probe",
            return_value={
                "ok": False,
                "outcome": "device_rejected",
                "method": "ipod_sg_raw",
                "detail": "DEVICE_REJECTED SCSI status 0x2",
            },
        ):
            with self.assertRaises(Refused) as ctx:
                verify_untether(nano, serial="XYZ123ABC456")
        msg = str(ctx.exception)
        self.assertIn("C6 96", msg)
        self.assertIn("install-untether", msg)
        self.assertIn("Helvetica.ttf", msg)
        self.assertIn("/Apps/Startup/ab.hbapp", msg)
        self.assertIn("CHECK CONDITION is expected", msg)
        self.assertNotIn("after Helvetica it must succeed", msg)
        self.assertNotIn("handler is not active", msg)
        self.assertNotIn("could not send", msg.lower())

    def test_verify_c6_reject_with_apps_present_does_not_reflash(self):
        vol = Path(self.td.name) / "withapps"
        startup = vol / "Apps" / "Startup"
        startup.mkdir(parents=True)
        (vol / "Apps" / "AllApps.pack").write_bytes(b"HBPK")
        (startup / "resident.hbapp").write_bytes(b"res")
        nano = _nano(mountpoints=[str(vol)])
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = False
        layout.scheme = "mbr"
        layout.summary.return_value = "scheme mbr tested Finder map"
        with mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.check_usb_serial"
        ), mock.patch("host.untether._layout_for_nano", return_value=layout), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch(
            "host.scsi.harness_probe",
            return_value={
                "ok": False,
                "outcome": "device_rejected",
                "method": "ipod_sg_raw",
                "detail": "DEVICE_REJECTED SCSI status 0x2",
            },
        ):
            with self.assertRaises(Refused) as ctx:
                verify_untether(nano, serial="XYZ123ABC456")
        msg = str(ctx.exception)
        self.assertIn("hashes do not match", msg)
        self.assertIn("Do not re-flash", msg)
        self.assertIn("install-untether", msg)
        self.assertIn("CHECK CONDITION is expected", msg)
        self.assertNotIn("star harness is not active", msg)

    def test_verify_refuses_could_not_send_without_claiming_inactive(self):
        nano = _nano()
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = False
        layout.scheme = "mbr"
        layout.summary.return_value = "scheme mbr tested Finder map"
        with mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.check_usb_serial"
        ), mock.patch("host.untether._layout_for_nano", return_value=layout), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch(
            "host.scsi.harness_probe",
            return_value={
                "ok": False,
                "outcome": "could_not_send",
                "method": "ipod_sg_raw",
                "detail": "COULD_NOT_SEND CreateSCSITask failed",
            },
        ):
            with self.assertRaises(Refused) as ctx:
                verify_untether(nano, serial="XYZ123ABC456")
        msg = str(ctx.exception)
        self.assertIn("could not send", msg.lower())
        self.assertIn("does not prove", msg.lower())
        self.assertIn("iPodSBCNub", msg)
        self.assertIn("USBInterfaceOpenSeize", msg)
        self.assertIn("LIBUSB_ERROR_ACCESS", msg)
        self.assertIn("DADiskClaim", msg)
        self.assertNotIn("handler is not active", msg)
        self.assertNotIn(UNTETHER_VERIFY_INCONCLUSIVE, msg)

    def test_verify_ok_when_fat_hashes_match_even_if_scsi_could_not_send(self):
        from host.state import read_receipt
        from host.untether import UNTETHER_VERIFY_INCONCLUSIVE as INC

        vol = Path(self.td.name) / "vol"
        _plant_tested_apps(vol)
        nano = _nano(mountpoints=[str(vol)])
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = False
        layout.scheme = "mbr"
        layout.summary.return_value = "scheme mbr tested Finder map"
        with mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.check_usb_serial"
        ), mock.patch("host.untether._layout_for_nano", return_value=layout), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch(
            "host.scsi.harness_probe",
            return_value={
                "ok": False,
                "outcome": "could_not_send",
                "method": "ipod_sg_raw",
                "detail": "ipod_sg_raw: COULD_NOT_SEND CreateSCSITask failed\n"
                "usbscsi: claim attempt 1/4\n"
                "usbscsi:   libusb: access denied [code -3] (LIBUSB_ERROR_ACCESS)",
                "tried": ["ipod_sg_raw", "usbscsi"],
            },
        ):
            out = verify_untether(nano, serial="XYZ123ABC456")
        self.assertTrue(out["ok"])
        self.assertTrue(out["hashes"]["ok"])
        self.assertFalse(out["probe"]["ok"])
        rec = read_receipt(INC)
        self.assertEqual(rec.get("loader"), "yes")
        self.assertEqual(rec.get("scsi_ok"), "no")
        self.assertEqual(rec.get("hashes"), "yes")
        self.assertTrue((Path(self.td.name) / UNTETHER_VERIFY_OK).is_file())
        hashes = check_loader_hashes(nano)
        self.assertTrue(hashes["ok"])

    def test_verify_ok_when_c6_rejected_and_hashes_match(self):
        vol = Path(self.td.name) / "c6vol"
        _plant_tested_apps(vol)
        nano = _nano(mountpoints=[str(vol)])
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = False
        layout.scheme = "mbr"
        layout.summary.return_value = "scheme mbr tested Finder map"
        with mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.check_usb_serial"
        ), mock.patch("host.untether._layout_for_nano", return_value=layout), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch(
            "host.scsi.harness_probe",
            return_value={
                "ok": False,
                "outcome": "device_rejected",
                "method": "ipod_sg_raw",
                "detail": "DEVICE_REJECTED SCSI status 0x2 CHECK CONDITION",
            },
        ):
            out = verify_untether(nano, serial="XYZ123ABC456")
        self.assertTrue(out["ok"])
        self.assertTrue(out["hashes"]["ok"])
        self.assertFalse(out["probe"]["ok"])
        self.assertTrue((Path(self.td.name) / UNTETHER_VERIFY_OK).is_file())

    def test_verify_ok_when_harness_present(self):
        root = Path(self.td.name) / "vol"
        _plant_tested_apps(root)
        nano = _nano(mountpoints=[str(root)])
        vol = root / "iPod_Control" / "Music" / "F00"
        vol.mkdir(parents=True)
        (vol / "song.mp3").write_bytes(b"id3")
        itunes = root / "iPod_Control" / "iTunes"
        itunes.mkdir(parents=True)
        itlp = itunes / "iTunes Library.itlp"
        itlp.mkdir()
        import sqlite3

        for name in ("Library.itdb", "Dynamic.itdb"):
            dbp = itlp / name
            con = sqlite3.connect(dbp)
            con.execute("CREATE TABLE item (id INTEGER)")
            con.commit()
            con.close()
        (itunes / "iTunesCDB").write_bytes(b"mhbd" + b"\x00" * 128)
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = False
        layout.scheme = "mbr"
        layout.summary.return_value = "scheme mbr tested Finder map"
        with mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.check_usb_serial"
        ), mock.patch("host.untether._layout_for_nano", return_value=layout), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch(
            "host.scsi.harness_probe",
            return_value={
                "ok": True,
                "outcome": "ok",
                "method": "ipod_sg_raw",
                "detail": "C6_96_04_OK",
            },
        ), mock.patch("host.scsi.peek", return_value=b"\x01" + b"\x00" * 0x1FF):
            out = verify_untether(nano, serial="XYZ123ABC456")
        self.assertTrue(out["ok"])
        self.assertTrue(out["probe"]["ok"])
        self.assertTrue(out["music"]["ok"])
        self.assertGreaterEqual(out["music"]["songs"], 1)
        self.assertTrue(out["library_db"]["ok"])

    def test_verify_macos_unmounts_then_remounts(self):
        vol = Path(self.td.name) / "vol"
        _plant_tested_apps(vol)
        music = vol / "iPod_Control" / "Music" / "F00"
        music.mkdir(parents=True)
        (music / "song.mp3").write_bytes(b"id3")
        nano = _nano(mountpoints=[str(vol)])
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = True
        layout.scheme = "mbr"
        layout.summary.return_value = "ipodrepartition 4k fat32"
        calls = []

        def fake_unmount(_nano, **kw):
            calls.append("unmount")
            unmount_kw.append(kw)

        mount_kw = []
        unmount_kw = []
        finder_calls = []

        def fake_mount(_nano, **kw):
            calls.append("mount")
            mount_kw.append(kw)
            return vol

        def fake_finder(**kw):
            finder_calls.append(kw)
            return 2

        with mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.check_usb_serial"
        ), mock.patch("host.untether._layout_for_nano", return_value=layout), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch("host.untether.is_macos", return_value=True), mock.patch(
            "host.untether.unmount", side_effect=fake_unmount
        ), mock.patch(
            "host.untether.ensure_fat_mount", side_effect=fake_mount
        ), mock.patch(
            "host.untether.close_finder_ipod_windows", side_effect=fake_finder
        ), mock.patch("host.untether.time.sleep"), mock.patch(
            "host.scsi.ensure_scsi_helpers"
        ), mock.patch(
            "host.scsi.harness_probe",
            return_value={
                "ok": True,
                "outcome": "ok",
                "method": "ipod_sg_raw",
                "detail": "C6_96_04_OK",
            },
        ), mock.patch("host.scsi.peek", return_value=b"\x01" + b"\x00" * 0x1FF):
            out = verify_untether(nano, serial="XYZ123ABC456")
        self.assertTrue(out["ok"])
        self.assertEqual(calls, ["mount", "unmount", "mount"])
        self.assertEqual(mount_kw[-1].get("retries"), 12)
        self.assertEqual(mount_kw[-1].get("delay"), 0.5)
        self.assertTrue(unmount_kw[0].get("force"))
        self.assertEqual(len(finder_calls), 1)

    def test_verify_prints_full_scsi_detail_not_trimmed(self):
        nano = _nano()
        layout = mock.Mock()
        layout.is_ipodrepartition_4k_fat32.return_value = False
        layout.scheme = "mbr"
        layout.summary.return_value = "scheme mbr tested Finder map"
        long_detail = (
            "ipod_sg_raw: using com_apple_driver_iPodSBCNub\n"
            "ipod_sg_raw: ObtainExclusiveAccess kr=0x00000000 (success)\n"
            "ipod_sg_raw: COULD_NOT_SEND CreateSCSITask failed "
            "(NULL; exclusive may not be held, or IOCFPlugIn was released). "
            "IOBlockStorageServices/IOMedia clients can deny exclusive even "
            "after diskutil unmountDisk.\n"
            "usbscsi: Darwin USBInterfaceOpenSeize nudge did not seize "
            "(libusb claim still tried)\n"
            "usbscsi: SetAutoDetach ok\n"
            "usbscsi: claim attempt 1/4\n"
            "usbscsi:   libusb: access denied [code -3] (LIBUSB_ERROR_ACCESS; "
            "IOUSBMassStorageDriver still owns iface 0)\n"
            "usbscsi: claimed iface=0 cfg=1 IN=0x83 OUT=0x02 extra padding "
            + ("x" * 80)
        )
        self.assertGreater(len(long_detail), 240)
        with mock.patch("host.fat.assert_not_hfs"), mock.patch(
            "host.untether.check_usb_serial"
        ), mock.patch("host.untether._layout_for_nano", return_value=layout), mock.patch(
            "host.untether.check_disk_size",
            return_value={"size": TESTED_DISK_SIZE, "hardware_tested": True},
        ), mock.patch(
            "host.scsi.harness_probe",
            return_value={
                "ok": False,
                "outcome": "could_not_send",
                "method": "ipod_sg_raw",
                "detail": long_detail,
                "tried": ["ipod_sg_raw", "usbscsi"],
            },
        ), mock.patch("builtins.print") as printed:
            with self.assertRaises(Refused):
                verify_untether(nano, serial="XYZ123ABC456")
        blob = "\n".join(str(c.args[0]) if c.args else "" for c in printed.call_args_list)
        self.assertIn("CreateSCSITask failed", blob)
        self.assertIn("LIBUSB_ERROR_ACCESS", blob)
        self.assertIn("claimed iface=0", blob)
        self.assertIn("x" * 80, blob)
        self.assertIn("Home and Settings look stock", blob)

    def test_apps_markers_loader_and_lasttrace(self):
        vol = Path(self.td.name) / "appsvol"
        startup = vol / "Apps" / "Startup"
        exe = vol / "Apps" / "Executables"
        icons = vol / "Apps" / "Icons"
        startup.mkdir(parents=True)
        exe.mkdir()
        icons.mkdir()
        (vol / "Apps" / "AllApps.pack").write_bytes(b"HBPK")
        (startup / "resident.hbapp").write_bytes(b"res")
        (startup / "lasttrace.bin").write_bytes(b"trace")
        (exe / "Blocks.hbapp").write_bytes(b"b")
        (icons / "Blocks.bin").write_bytes(b"i")
        nano = _nano(mountpoints=[str(vol)])
        out = apps_markers(nano)
        self.assertTrue(out["ok"])
        self.assertTrue(out["lasttrace"])
        self.assertIn("Blocks", out["icons"])
        self.assertIn("/Apps/AllApps.pack", out["present"])
        self.assertIn("/Apps/Startup/lasttrace.bin", out["present"])
        empty = apps_markers(_nano(mountpoints=[]))
        self.assertFalse(empty["ok"])
        self.assertFalse(empty["lasttrace"])

    def test_volume_root_listing_hidden_and_missing_apps(self):
        vol = Path(self.td.name) / "liveipod"
        vol.mkdir()
        (vol / ".Spotlight-V100").mkdir()
        (vol / ".VolumeIcon.icns").write_bytes(b"icns")
        (vol / ".fseventsd").mkdir()
        (vol / ".metadata_never_index").write_bytes(b"")
        device = vol / "iPod_Control" / "Device"
        device.mkdir(parents=True)
        (device / "SysInfo").write_text("ModelNumStr: MKN52ZP\n")
        listing = volume_root_listing(vol)
        names = [e["name"] for e in listing["entries"]]
        self.assertIn(".Spotlight-V100", names)
        self.assertIn(".VolumeIcon.icns", names)
        self.assertIn(".fseventsd", names)
        self.assertIn(".metadata_never_index", names)
        self.assertIn("iPod_Control", names)
        self.assertIsNone(listing["apps_name"])
        self.assertTrue(listing["sysinfo"])
        self.assertIn("SysInfo", listing["device_names"])
        nano = _nano(mountpoints=[str(vol)])
        out = apps_markers(nano)
        self.assertFalse(out["ok"])
        self.assertIsNone((out.get("listing") or {}).get("apps_name"))

    def test_apps_markers_case_insensitive_apps_dir(self):
        vol = Path(self.td.name) / "mixed"
        apps = vol / "apps"
        startup = apps / "Startup"
        startup.mkdir(parents=True)
        (apps / "AllApps.pack").write_bytes(b"HBPK")
        (startup / "resident.hbapp").write_bytes(b"res")
        listing = volume_root_listing(vol)
        self.assertEqual(listing["apps_name"], "apps")
        out = apps_markers(_nano(mountpoints=[str(vol)]))
        self.assertTrue(out["ok"])
        self.assertIn("/Apps/AllApps.pack", out["present"])

    def test_verify_source_does_not_trim_helper_stderr(self):
        text = (ROOT / "host" / "untether.py").read_text(encoding="utf-8")
        self.assertNotIn("[:240]", text)
        self.assertIn("print_probe_detail", text)
        self.assertIn("UNTETHER_VERIFY_INCONCLUSIVE", text)
        self.assertIn("close_finder_ipod_windows", text)
        self.assertIn("print_screen_verify_note", text)
        self.assertIn("apps_markers", text)
        self.assertIn("durable_flush_and_rehash", text)
        self.assertIn("leave_mounted=False", text)
        self.assertIn("volume_root_listing", text)
        self.assertIn("install-untether", text)
        self.assertIn("Helvetica.ttf", text)
        self.assertIn("/Apps/Startup/ab.hbapp", text)
        self.assertIn("CHECK CONDITION is expected", text)
        self.assertIn("expected_loader_hashes", text)
        self.assertNotIn("after Helvetica it must succeed", text)
        self.assertNotIn("star harness is not active", text)

    def test_ipodrepartition_run_requires_2015_flag(self):
        nano = _nano(size=IPODREPARTITION_DISK_SIZE, raw_device=str(self.backup))
        with mock.patch(
            "host.untether.check_untether_device", return_value={"check_only": True}
        ), mock.patch(
            "host.untether.check_mse", return_value=const.FIRMWARE_MSE_SHA256
        ), mock.patch(
            "host.mse.inspect_mse_flash_identity", return_value=_ident()
        ), mock.patch("host.untether.check_backup"), mock.patch(
            "host.untether.install_loader_files", return_value={}
        ):
            with self.assertRaises(Refused) as ctx:
                write_untether_firmware(
                    nano,
                    mse=self.mse,
                    serial="XYZ123ABC456",
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    accept_2015_refresh=False,
                    accept_untested_flasher=True,
                    typed_confirm="UNTETHER",
                )
        self.assertIn("2015", str(ctx.exception))


class IpodSgRawSourceTests(unittest.TestCase):
    def test_probe_peek_only_no_write_exec(self):
        text = SG_RAW_C.read_text(encoding="utf-8")
        self.assertTrue(SG_RAW_C.is_file())
        self.assertIn("SCSITask", text)
        self.assertIn("com_apple_driver_iPodSBCNub", text)
        self.assertIn("iPodUserClientDevice", text)
        self.assertIn("ObtainExclusiveAccess", text)
        self.assertIn("COULD_NOT_SEND", text)
        self.assertIn("DEVICE_REJECTED", text)
        self.assertIn("0xC6", text)
        self.assertIn("0x96", text)
        self.assertIn("0x04", text)
        self.assertIn("0x02", text)
        self.assertIn("C6_96_04_OK", text)
        self.assertIn("No WRITE(10)", text)
        self.assertIn("inquiry|probe|peek", text)
        self.assertIn("pad_cdb_len", text)
        self.assertIn("DADiskClaim", text)
        self.assertIn("0x%08x", text)
        self.assertIn("CreateSCSITask", text)
        self.assertIn("g_plugin", text)
        self.assertIn("DiskArbitration.h", text)
        self.assertIn("IODestroyPlugInInterface", text)
        self.assertIn("no DKIOCSCSIUSERCMD", text)
        self.assertIn("QA1179", text)
        self.assertIn("Live test", text)
        self.assertIn("REQUEST SENSE", text)
        self.assertIn("CDB 0x03", text)
        self.assertIn("kSCSITaskStatus_CHECK_CONDITION", text)
        self.assertNotIn("\u2014", text)
        self.assertNotRegex(text, r'strcmp\(cmd, "write"\)')
        self.assertNotRegex(text, r'strcmp\(cmd, "exec"\)')
        self.assertNotIn("WRITE(10)", text.replace("No WRITE(10)", ""))
        self.assertNotIn("0x2A", text)

    def test_scsi_write_exec_refused_on_ipod_sg_raw(self):
        from host.scsi import exec_addr, write_mem

        nano = _nano()
        with mock.patch("host.scsi._need_method", return_value="ipod_sg_raw"):
            with self.assertRaises(Refused) as ctx:
                write_mem(nano, 0x09120000, b"x")
            self.assertIn("probe/peek only", str(ctx.exception))
            with self.assertRaises(Refused) as ctx2:
                exec_addr(nano, 0x09120000)
            self.assertIn("probe/peek only", str(ctx2.exception))


class UsbscsiSourceTests(unittest.TestCase):
    def test_probe_and_seize_and_csw_status_1(self):
        folder = ROOT / "host" / "cmd" / "usbscsi"
        main = (folder / "main.go").read_text(encoding="utf-8")
        logic = (folder / "logic.go").read_text(encoding="utf-8")
        seize = (folder / "seize_darwin.go").read_text(encoding="utf-8")
        self.assertIn('case "probe":', main)
        self.assertIn("openDiskBotOnce", main)
        self.assertIn("nudgeKernelUSB", main)
        self.assertIn("SCSI CHECK CONDITION CSW status 1", main)
        self.assertIn("SCSI CSW status %d", main)
        self.assertIn("REQUEST SENSE", main)
        self.assertIn("0x03", main)
        self.assertIn("formatCheckCondition", logic)
        self.assertIn("Live test", main)
        self.assertIn("SetAutoDetach (kernel detach) failed, continuing", main)
        self.assertIn("printClaimAttempt", main)
        self.assertIn("LIBUSB_ERROR_ACCESS", main)
        self.assertIn("USBInterfaceOpenSeize", seize)
        self.assertIn("USBDeviceOpenSeize", seize)
        self.assertIn("kr=0x%08x", seize)
        self.assertIn("checkConditionRe", logic)
        self.assertIn("unknown command", logic)
        self.assertIn("formatLibusbErr", logic)
        self.assertIn("LIBUSB_ERROR_ACCESS", logic)
        self.assertNotIn("\u2014", main + logic + seize)


class ReadmeHardware2015Tests(unittest.TestCase):
    def test_readme_records_bootloader_breakthrough(self):
        text = (ROOT / "docs" / "HARDWARE-2015.md").read_text(encoding="utf-8")
        for needle in ("39A10023", "Mid-2015", "--i-accept-2015-refresh", "USB-A hub"):
            self.assertIn(needle, text)
        self.assertNotIn("\u2014", text)


if __name__ == "__main__":
    unittest.main()
