#!/usr/bin/env python3
"""2015 bootloader extract, family mapping, and write-path refuses."""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from host import const
from host.bootloader import (
    extract_stock_bootloader,
    nano7late_updater_family_id,
    require_family_39,
    run_bootloader_command,
    write_bootloader,
)
from host.compat import Refused, sha256_bytes
from host.safety import bootloader_identity, check_bootloader

START = ROOT / "start"
PATCH = ROOT / "untether" / "patches" / "wind3x-nano7.patch"


def _dummy_ipsw(path: Path, payload: bytes = b"n31-bootloader-test") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("N31.bootloader.rb3", payload)
        z.writestr("Firmware.mse", b"mse")
    return path


class FamilyMappingTests(unittest.TestCase):
    def test_patch_maps_nano7late_to_family_39(self):
        family = nano7late_updater_family_id(PATCH)
        self.assertEqual(family, 39)
        self.assertEqual(require_family_39(PATCH), const.NANO7LATE_UPDATER_FAMILY_ID)
        text = PATCH.read_text()
        self.assertIn("UpdaterFamilyID: 39", text)
        self.assertIn("--bootloader-only", text)
        self.assertIn("37A40005", text)
        self.assertIn(const.BOOTLOADER_39A10023_SHA256, text)
        self.assertIn("nano7LateRetailIPSW", text)
        self.assertNotIn("cmd_nand_read", text)
        self.assertNotIn("exploit.RCE", text)

    def test_wind3x_patch_refuses_37a40005_url(self):
        text = PATCH.read_text()
        self.assertIn("RefuseNano7Late37A40005", text)
        self.assertIn("never download 37A40005", text.lower() + text)
        self.assertIn("iPod_1.1.2_39A10023.ipsw", text)


class HashPinTests(unittest.TestCase):
    def test_identity_pins(self):
        self.assertEqual(
            bootloader_identity(const.BOOTLOADER_39A10023_SHA256), "39A10023"
        )
        self.assertEqual(
            bootloader_identity(const.BOOTLOADER_37A40005_SHA256_PREFIX + "0" * 56),
            "37A40005",
        )
        self.assertEqual(bootloader_identity("deadbeef"), "unknown")

    def test_check_bootloader_refuses_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "N31.bootloader.rb3"
            path.write_bytes(b"wrong")
            with self.assertRaises(Refused) as ctx:
                check_bootloader(path)
            self.assertIn("hash mismatch", str(ctx.exception))

    def test_check_bootloader_refuses_37a40005_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "N31.bootloader.rb3"
            path.write_bytes(b"2012")
            fake = const.BOOTLOADER_37A40005_SHA256_PREFIX + "ab" * 28
            with mock.patch("host.safety.sha256_file", return_value=fake):
                with self.assertRaises(Refused) as ctx:
                    check_bootloader(path)
            self.assertIn("37A40005", str(ctx.exception))
            self.assertIn("nothing written", str(ctx.exception).lower())

    def test_extract_refuses_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            ipsw = _dummy_ipsw(Path(td) / "fake.ipsw", b"not-the-pin")
            with mock.patch("host.firmware.build_dir", return_value=Path(td)):
                with self.assertRaises(Refused) as ctx:
                    extract_stock_bootloader(ipsw)
            self.assertIn("hash mismatch", str(ctx.exception))

    def test_extract_accepts_matching_override(self):
        payload = b"n31-bootloader-test"
        digest = sha256_bytes(payload)
        with tempfile.TemporaryDirectory() as td:
            ipsw = _dummy_ipsw(Path(td) / "fake.ipsw", payload)
            with mock.patch("host.firmware.build_dir", return_value=Path(td)):
                dest = extract_stock_bootloader(ipsw, expected_sha256=digest)
            self.assertTrue(dest.is_file())
            self.assertEqual(sha256_bytes(dest.read_bytes()), digest)
            self.assertEqual(check_bootloader(dest, expected_sha256=digest), digest)

    def test_extract_refuses_37a40005_payload(self):
        payload = b"2012-bootloader"
        digest = const.BOOTLOADER_37A40005_SHA256_PREFIX + "cd" * 28
        with tempfile.TemporaryDirectory() as td:
            ipsw = _dummy_ipsw(Path(td) / "fake.ipsw", payload)
            with mock.patch("host.firmware.build_dir", return_value=Path(td)), mock.patch(
                "host.bootloader.sha256_bytes", return_value=digest
            ):
                with self.assertRaises(Refused) as ctx:
                    extract_stock_bootloader(ipsw, expected_sha256=digest)
            self.assertIn("37A40005", str(ctx.exception))


class WritePathRefuseTests(unittest.TestCase):
    def test_write_refuses_hash_mismatch_before_usb(self):
        with tempfile.TemporaryDirectory() as td:
            ipsw = _dummy_ipsw(Path(td) / "fake.ipsw", b"nope")
            with mock.patch("host.firmware.build_dir", return_value=Path(td)), mock.patch(
                "host.bootloader.apple_pids", side_effect=AssertionError("USB must not be touched")
            ):
                with self.assertRaises(Refused) as ctx:
                    write_bootloader(
                        serial="XYZ123ABC456",
                        accept_untested_flasher=True,
                        accept_2015_refresh=True,
                        typed_confirm="BOOTLOADER",
                        ipsw=ipsw,
                    )
            self.assertIn("hash mismatch", str(ctx.exception))

    def test_write_refuses_wtf_0x1249(self):
        payload = b"n31-bootloader-test"
        digest = sha256_bytes(payload)
        with tempfile.TemporaryDirectory() as td:
            ipsw = _dummy_ipsw(Path(td) / "fake.ipsw", payload)
            with mock.patch("host.firmware.build_dir", return_value=Path(td)), mock.patch(
                "host.bootloader.apple_pids", return_value=[const.NANO7_WTF_PID]
            ), mock.patch("host.bootloader.recovery_usb_state", return_value="wtf"):
                with self.assertRaises(Refused) as ctx:
                    write_bootloader(
                        serial="XYZ123ABC456",
                        accept_untested_flasher=True,
                        accept_2015_refresh=True,
                        typed_confirm="BOOTLOADER",
                        ipsw=ipsw,
                        expected_sha256=digest,
                    )
            self.assertIn("0x1249", str(ctx.exception))
            self.assertIn("2012", str(ctx.exception))

    def test_write_refuses_diskmode_without_0x124a(self):
        payload = b"n31-bootloader-test"
        digest = sha256_bytes(payload)
        with tempfile.TemporaryDirectory() as td:
            ipsw = _dummy_ipsw(Path(td) / "fake.ipsw", payload)
            with mock.patch("host.firmware.build_dir", return_value=Path(td)), mock.patch(
                "host.bootloader.apple_pids", return_value=[const.NANO7_DISK_PID]
            ), mock.patch("host.bootloader.recovery_usb_state", return_value="disk"):
                with self.assertRaises(Refused) as ctx:
                    write_bootloader(
                        serial="XYZ123ABC456",
                        accept_untested_flasher=True,
                        accept_2015_refresh=True,
                        typed_confirm="BOOTLOADER",
                        ipsw=ipsw,
                        expected_sha256=digest,
                    )
            self.assertIn("1267", str(ctx.exception))
            self.assertIn("DFU", str(ctx.exception))

    def test_write_argv_is_bootloader_only(self):
        payload = b"n31-bootloader-test"
        digest = sha256_bytes(payload)
        fake_bin = Path("/tmp/wInd3x-fake")
        log = (
            "Found device device=\"Nano 7G (Mid-2015)\" interface=diskmode\n"
            "SerialNumber: XYZ123ABC456\n"
            "Using supplied bootloader identity=39A10023\n"
            "Writing bootloader only (no MSE, no repartition, no FAT)\n"
            "Bootloader write complete; no firmware and no partition were written\n"
        )
        seen = {}

        def fake_run(args, wind3x):
            seen["args"] = list(args)
            return subprocess.CompletedProcess(args, 0, log, "")

        with tempfile.TemporaryDirectory() as td:
            ipsw = _dummy_ipsw(Path(td) / "fake.ipsw", payload)
            Path(td, "state").mkdir()
            with mock.patch.dict(os.environ, {"NANO7_STATE_DIR": str(Path(td) / "state")}), mock.patch(
                "host.firmware.build_dir", return_value=Path(td)
            ), mock.patch(
                "host.bootloader.apple_pids", return_value=[const.NANO7_DFU_PID]
            ), mock.patch(
                "host.bootloader.recovery_usb_state", return_value="dfu"
            ), mock.patch(
                "host.bootloader.select_wind3x",
                return_value=(fake_bin, False, "fake"),
            ), mock.patch("host.bootloader.wait_dfu"), mock.patch(
                "host.bootloader.wait_recovery_entry"
            ), mock.patch("host.usb.run_wind3x", side_effect=fake_run):
                out = write_bootloader(
                    serial="XYZ123ABC456",
                    accept_untested_flasher=True,
                    accept_2015_refresh=True,
                    typed_confirm="BOOTLOADER",
                    ipsw=ipsw,
                    expected_sha256=digest,
                )
        self.assertTrue(out["ok"])
        argv = seen["args"]
        self.assertEqual(argv[0], "restore")
        self.assertIn("--bootloader-only", argv)
        self.assertIn("--bootloader", argv)
        self.assertNotIn("--full", argv)
        self.assertNotIn("--firmware", argv)
        self.assertNotIn("nand", " ".join(argv).lower())

    def test_check_only_never_writes(self):
        payload = b"n31-bootloader-test"
        digest = sha256_bytes(payload)
        prepare_log = (
            'Found device device="Nano 7G (Mid-2015)" interface=diskmode\n'
            "SerialNumber: XYZ123ABC456\n"
            "Recovery mode ready; no device partitions were written\n"
        )

        def boom(*a, **k):
            raise AssertionError("write path must not run on --check-only")

        with tempfile.TemporaryDirectory() as td:
            ipsw = _dummy_ipsw(Path(td) / "fake.ipsw", payload)
            Path(td, "state").mkdir()
            with mock.patch.dict(os.environ, {"NANO7_STATE_DIR": str(Path(td) / "state")}), mock.patch(
                "host.firmware.build_dir", return_value=Path(td)
            ), mock.patch(
                "host.bootloader.apple_pids", return_value=[const.NANO7_DFU_PID]
            ), mock.patch(
                "host.bootloader.recovery_usb_state", return_value="dfu"
            ), mock.patch(
                "host.bootloader.select_wind3x",
                return_value=(Path("/tmp/wInd3x-fake"), False, "fake"),
            ), mock.patch("host.bootloader.wait_dfu"), mock.patch(
                "host.bootloader.wait_recovery_entry"
            ), mock.patch(
                "host.bootloader.prepare_restore", return_value=prepare_log
            ), mock.patch("host.bootloader.write_bootloader", side_effect=boom), mock.patch(
                "host.usb.run_wind3x", side_effect=boom
            ):
                out = run_bootloader_command(
                    serial="XYZ123ABC456",
                    check_only=True,
                    run=False,
                    accept_untested_flasher=True,
                    accept_2015_refresh=True,
                    ipsw=ipsw,
                    expected_sha256=digest,
                )
        self.assertTrue(out["check_only"])
        self.assertIsNone(out["readback"])
        self.assertEqual(out["identity"], "39A10023" if digest == const.BOOTLOADER_39A10023_SHA256 else bootloader_identity(digest))

    def test_typed_confirm_bootloader_is_global(self):
        from host.cli import build_parser

        p = build_parser()
        ns = p.parse_args(
            [
                "--i-accept-untested-flasher",
                "--i-accept-2015-refresh",
                "--typed-confirm",
                "BOOTLOADER",
                "bootloader",
                "--run",
                "--serial",
                "XYZ123ABC456",
            ]
        )
        self.assertEqual(ns.typed_confirm, "BOOTLOADER")
        self.assertTrue(ns.run)
        self.assertEqual(ns.serial, "XYZ123ABC456")
        with self.assertRaises(SystemExit):
            p.parse_args(
                [
                    "bootloader",
                    "--run",
                    "--typed-confirm",
                    "BOOTLOADER",
                    "--serial",
                    "X",
                ]
            )

    def test_help_lists_bootloader(self):
        r = subprocess.run(
            [sys.executable, str(START), "--help"], capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("bootloader", r.stdout)
        self.assertNotIn("\u2014", r.stdout)


class RestoreFullBootloaderArgTests(unittest.TestCase):
    def test_restore_full_passes_local_bootloader_never_37a40005_url(self):
        from host.winpod import restore_full

        seen = {}

        def fake_run(args, wind3x):
            seen["args"] = list(args)
            log = (
                "Using supplied firmware\n"
                "Using supplied bootloader identity=39A10023\n"
                "Reformatting system partition... mib=184\n"
                "Writing bootloader...\n"
                "Writing firmware...\n"
                "Finalizing...\n"
            )
            return subprocess.CompletedProcess(args, 0, log, "")

        with tempfile.TemporaryDirectory() as td:
            mse = Path(td) / "Firmware-stock-39A10023.MSE"
            mse.write_bytes(b"mse")
            bl = Path(td) / "N31.bootloader-39A10023.rb3"
            bl.write_bytes(b"bl")
            Path(td, "state").mkdir()
            with mock.patch.dict(os.environ, {"NANO7_STATE_DIR": str(Path(td) / "state")}), mock.patch(
                "host.usb.run_wind3x", side_effect=fake_run
            ):
                restore_full(Path("/tmp/wInd3x"), mse, bootloader=bl)
        argv = seen["args"]
        self.assertIn("--full", argv)
        self.assertIn("--firmware", argv)
        self.assertIn("--bootloader", argv)
        self.assertEqual(argv[argv.index("--bootloader") + 1], str(bl))
        self.assertNotIn("37A40005", " ".join(argv))


if __name__ == "__main__":
    unittest.main()
