#!/usr/bin/env python3
"""One-command ./start install skips NAND when receipts exist."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host import const
from host.compat import Refused, sha256_file
from host.device import Nano, annotate_size
from host.install import (
    INSTALL_OK,
    bootloader_skip_reason,
    firmware_skip_reason,
    run_install,
)
from host.state import write_receipt
from host.const import TESTED_DISK_SIZE


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


class InstallSkipTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        os.environ["NANO7_STATE_DIR"] = self.td.name
        os.environ["NANO7KIT_CONFIG_DIR"] = str(Path(self.td.name) / "cfg")
        self.mse = Path(self.td.name) / "Firmware-untethered.MSE"
        self.mse.write_bytes(b"mse-stub")
        self.backup = Path(self.td.name) / "nano.img"
        self.backup.write_bytes(b"\0" * 1024)
        self.bhash = sha256_file(self.backup)

    def test_skip_reasons_from_receipts(self):
        self.assertIsNone(bootloader_skip_reason("XYZ123ABC456"))
        write_receipt(
            "BOOTLOADER_OK",
            {
                "serial": "XYZ123ABC456",
                "identity": "39A10023",
                "bootloader_sha256": const.BOOTLOADER_39A10023_SHA256,
            },
        )
        why = bootloader_skip_reason("XYZ123ABC456")
        self.assertIsNotNone(why)
        self.assertIn("BOOTLOADER_OK", why)
        self.assertIn("39A10023", why)
        self.assertIsNone(firmware_skip_reason(const.FIRMWARE_MSE_SHA256))
        write_receipt("UNTETHER_OK", {"serial": "XYZ123ABC456"})
        why_fw = firmware_skip_reason(const.FIRMWARE_MSE_SHA256)
        self.assertIsNotNone(why_fw)
        self.assertIn("FIRMWARE_ATTEMPTED", why_fw)

    def test_install_without_run_skips_nand_and_copies_apps(self):
        write_receipt(
            "BOOTLOADER_OK",
            {"serial": "XYZ123ABC456", "identity": "39A10023"},
        )
        write_receipt("UNTETHER_OK", {"serial": "XYZ123ABC456"})
        seen = {}

        def boom(*_a, **_k):
            raise AssertionError("NAND write must not run")

        def fake_loader(*_a, **kw):
            seen["leave_mounted"] = kw.get("leave_mounted")
            return {}

        def fake_verify(*_a, **_k):
            seen["verify"] = True
            return {"ok": True}

        nano = _nano()
        with mock.patch(
            "host.untether.check_untether_device", return_value={"check_only": True}
        ), mock.patch(
            "host.safety.check_mse", return_value=const.FIRMWARE_MSE_SHA256
        ), mock.patch(
            "host.untether.install_loader_files", side_effect=fake_loader
        ), mock.patch(
            "host.untether.verify_untether", side_effect=fake_verify
        ), mock.patch(
            "host.bootloader.write_bootloader", side_effect=boom
        ), mock.patch(
            "host.untether.write_untether_firmware", side_effect=boom
        ):
            out = run_install(
                nano,
                mse=self.mse,
                serial="XYZ123ABC456",
                backup=self.backup,
                backup_sha256=self.bhash,
                run=False,
                accept_2015_refresh=True,
                accept_untested_flasher=True,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["bootloader"], "skip")
        self.assertEqual(out["firmware"], "skip")
        self.assertEqual(out["apps"], "yes")
        self.assertTrue(seen.get("verify"))
        self.assertIs(seen.get("leave_mounted"), True)
        self.assertTrue((Path(self.td.name) / INSTALL_OK).is_file())

    def test_install_run_refuses_wrong_typed_word_when_firmware_needed(self):
        write_receipt(
            "BOOTLOADER_OK",
            {"serial": "XYZ123ABC456", "identity": "39A10023"},
        )
        nano = _nano()
        with mock.patch(
            "host.untether.check_untether_device", return_value={"check_only": True}
        ), mock.patch(
            "host.safety.check_mse", return_value=const.FIRMWARE_MSE_SHA256
        ):
            with self.assertRaises(Refused) as ctx:
                run_install(
                    nano,
                    mse=self.mse,
                    serial="XYZ123ABC456",
                    backup=self.backup,
                    backup_sha256=self.bhash,
                    run=True,
                    typed_confirm="FLASH",
                    accept_2015_refresh=True,
                    accept_untested_flasher=True,
                )
        self.assertIn("UNTETHER", str(ctx.exception))

    def test_source_mentions_durable_and_skips(self):
        text = (ROOT / "host" / "install.py").read_text(encoding="utf-8")
        self.assertIn("BOOTLOADER_OK", text)
        self.assertIn("FIRMWARE_ATTEMPTED", text)
        self.assertIn("install_loader_files", text)
        self.assertIn("verify_untether", text)
        self.assertIn("leave_mounted=True", text)
        self.assertNotIn("\u2014", text)


class InstallCliTests(unittest.TestCase):
    def test_help_lists_install(self):
        import subprocess

        r = subprocess.run(
            [sys.executable, str(ROOT / "start"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("install", r.stdout)
        self.assertIn("roms", r.stdout)

    def test_install_help(self):
        import subprocess

        r = subprocess.run(
            [sys.executable, str(ROOT / "start"), "install", "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("bootloader-if-needed", r.stdout)
        self.assertIn("verify", r.stdout)
        self.assertNotIn("\u2014", r.stdout)


if __name__ == "__main__":
    unittest.main()
